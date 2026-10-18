"""Environment circuit breaker: stop sending traffic to an environment answering 5xx.

When an environment starts failing, a suite that keeps driving test after test
into it produces a wall of red that says nothing about the product and may
make the environment worse. The first 5xx the configured environment returns
— whether seen by an API client or by the browser — opens the breaker until the
run ends, and each later test skips with that reason instead.

* Only answers from the configured hosts are counted. A connection that never
  completed is not an answer from the environment and leaves the breaker closed.
* A test that provokes a 5xx on purpose declares it with
  :meth:`ServerErrorBreaker.expect_server_error`; the exemption covers just
  the matching calls made by that thread within the block.
* A 500 that is the environment reporting a *third party behind it* timing
  out can be exempted up to a per-run limit through ``upstream_timeout`` (see
  :func:`problem_detail_upstream_timeout`); past the limit it trips anyway,
  because repeated upstream timeouts leave the environment unusable all the
  same.

Run-wide across xdist workers: with ``trip_file`` set (the plugin sets it,
keyed by the run id), a trip in one worker is written to disk and every other
worker sees it before its next test. The upstream-timeout allowance is counted
per process.

Once tripped it never resets within a run (:meth:`reset` exists for unit tests).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from core.logger import logger
from gate._env import env_bool, env_int, env_list

ENABLED_ENV = "CIRCUIT_BREAKER"
HOSTS_ENV = "CIRCUIT_BREAKER_HOSTS"
UPSTREAM_LIMIT_ENV = "CIRCUIT_BREAKER_UPSTREAM_LIMIT"

UpstreamClassifier = Callable[[int, str | None], str | None]


#: Header set by ``checks.faults`` on every response it manufactures.
FAULT_INJECTED_HEADER = "x-fault-injected"


def _fault_injected(headers: object) -> bool:
    items = getattr(headers, "items", None)
    if not callable(items):
        return False
    return any(str(k).lower() == FAULT_INJECTED_HEADER for k, _ in items())


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def problem_detail_upstream_timeout(path_fragment: str) -> UpstreamClassifier:
    """Classifier for an RFC 7807 problem body reporting a read timeout to ``path_fragment``.

    Returns the dependency (host + path) when a 500's ``detail`` says a call to a
    URL containing ``path_fragment`` timed out while *reading* — a slow third
    party. A connect timeout is not exempt: not reaching the dependency at all
    is the environment failing.
    """

    def classify(status: int, body: str | None) -> str | None:
        if status != 500 or not body:
            return None
        try:
            problem = json.loads(body)
        except ValueError:
            return None
        if not isinstance(problem, dict):
            return None
        detail = str(problem.get("detail") or "")
        if "read timed out" not in detail.lower():
            return None
        for token in detail.replace('"', " ").replace("<", " ").replace(">", " ").split():
            if token.startswith(("http://", "https://")) and path_fragment in token:
                parts = urlsplit(token)
                return f"{parts.hostname}{parts.path}"
        return None

    return classify


class ToleratedServerError:
    """A declared, thread-scoped 5xx exemption; leaving the ``with`` block ends it."""

    def __init__(self, breaker: ServerErrorBreaker, url_fragment: str, reason: str):
        self._breaker = breaker
        self.url_fragment = url_fragment
        self.reason = reason
        self.exempted = 0

    def __enter__(self) -> ToleratedServerError:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        scopes = self._breaker._scopes()
        if self in scopes:
            scopes.remove(self)
        logger.info(
            "[circuit-breaker] stopped tolerating a 5xx from %r (%d exempted)",
            self.url_fragment,
            self.exempted,
        )


class ServerErrorBreaker:
    def __init__(
        self,
        hosts: Iterable[str] = (),
        *,
        enabled: bool = True,
        upstream_timeout: UpstreamClassifier | None = None,
        upstream_limit: int = 3,
        trip_file: str | Path | None = None,
    ) -> None:
        self.hosts = frozenset(h for h in (_host(x) or x.lower() for x in hosts) if h)
        self.enabled = enabled
        self.upstream_timeout = upstream_timeout
        self.upstream_limit = upstream_limit
        self.trip_file = Path(trip_file) if trip_file else None
        self._tripped: str | None = None
        self._upstream_seen = 0
        self._lock = threading.Lock()
        self._local = threading.local()

    @classmethod
    def from_env(
        cls,
        trip_file: str | Path | None = None,
        fallback_hosts: Iterable[str] = (),
        **kwargs,
    ) -> ServerErrorBreaker:
        """Hosts from ``CIRCUIT_BREAKER_HOSTS`` (comma separated), else ``fallback_hosts``."""
        return cls(
            hosts=env_list(HOSTS_ENV, ()) or tuple(fallback_hosts),
            enabled=env_bool(ENABLED_ENV, True),
            upstream_limit=env_int(UPSTREAM_LIMIT_ENV, 3),
            trip_file=trip_file,
            **kwargs,
        )

    # ------------------------------------------------------------------ recording

    def is_environment_url(self, url: str) -> bool:
        host = _host(url)
        return host is not None and host in self.hosts

    def record(
        self,
        source: str,
        method: str,
        url: str,
        status: int,
        body: str | None = None,
        *,
        headers: object = None,
    ) -> None:
        """Record one response; trips on a 5xx from the configured environment.

        A response carrying ``x-fault-injected`` (``checks.faults``) is a failure a test
        manufactured on purpose, not the environment answering, so it never trips.
        """
        if status < 500 or not self.enabled or not self.is_environment_url(url):
            return
        if _fault_injected(headers):
            logger.info(
                "[circuit-breaker] not tripped - %s %s -> %d was injected", method, url, status
            )
            return
        expected = self._expected_for(url)
        if expected is not None:
            expected.exempted += 1
            logger.warning(
                "[circuit-breaker] not tripped - %s %s %s -> %d was declared expected: %s",
                source,
                method,
                url,
                status,
                expected.reason,
            )
            return
        dependency = self.upstream_timeout(status, body) if self.upstream_timeout else None
        stamp = datetime.now(tz=timezone.utc).isoformat()
        if dependency is not None:
            with self._lock:
                self._upstream_seen += 1
                seen = self._upstream_seen
            if seen <= self.upstream_limit:
                logger.warning(
                    "[circuit-breaker] not tripped - %s %s -> %d is an upstream timeout (%s), "
                    "not the environment failing (%d/%d this run)",
                    method,
                    url,
                    status,
                    dependency,
                    seen,
                    self.upstream_limit,
                )
                return
            self._trip(
                f"{source} {method} {url} -> {status} - upstream timeout ({dependency}) "
                f"number {seen}, over the limit of {self.upstream_limit}, at {stamp}"
            )
            return
        self._trip(f"{source} {method} {url} -> {status} at {stamp}")

    def _trip(self, reason: str) -> None:
        with self._lock:
            if self._tripped is not None:
                return
            self._tripped = reason
        logger.error(
            "[circuit-breaker] TRIPPED - %s - remaining tests will not touch this environment",
            reason,
        )
        if self.trip_file is not None:
            try:
                self.trip_file.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive create: the first worker's reason is the one kept.
                with self.trip_file.open("x", encoding="utf-8") as handle:
                    handle.write(reason)
            except FileExistsError:
                pass
            except OSError as exc:
                logger.warning("[circuit-breaker] could not share the trip: %s", exc)

    # ------------------------------------------------------------------ exemptions

    def _scopes(self) -> list[ToleratedServerError]:
        scopes = getattr(self._local, "scopes", None)
        if scopes is None:
            scopes = self._local.scopes = []
        return scopes

    def _expected_for(self, url: str) -> ToleratedServerError | None:
        for scope in reversed(self._scopes()):
            if scope.url_fragment in url:
                return scope
        return None

    def expect_server_error(self, url_fragment: str, reason: str) -> ToleratedServerError:
        """Declare that a 5xx from calls containing ``url_fragment`` is this block's point."""
        if not url_fragment or not url_fragment.strip() or not reason or not reason.strip():
            raise ValueError(
                "declaring a tolerated 5xx requires both a URL fragment and a reason; "
                "an empty fragment would match every request"
            )
        scope = ToleratedServerError(self, url_fragment, reason)
        self._scopes().append(scope)
        logger.info("[circuit-breaker] tolerating a 5xx from %r: %s", url_fragment, reason)
        return scope

    # ------------------------------------------------------------------ state

    def tripped_reason(self) -> str | None:
        if self._tripped is not None:
            return self._tripped
        if self.trip_file is not None:
            try:
                shared = self.trip_file.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if shared:
                self._tripped = shared
                return shared
        return None

    def assume_not_tripped(self) -> None:
        """Skip the calling test, before it sends any traffic, once the breaker has tripped."""
        reason = self.tripped_reason()
        if reason is not None:
            pytest.skip(
                f"Skipped: the environment already returned a 5xx in this run ({reason}), "
                "so it receives no further requests."
            )

    def reset(self) -> None:
        """For unit tests only."""
        self._tripped = None
        self._upstream_seen = 0
        self._local = threading.local()


_default: ServerErrorBreaker = ServerErrorBreaker(enabled=False)


def default_breaker() -> ServerErrorBreaker:
    """The run's breaker, configured by the gate plugin; disabled until then."""
    return _default


def install_default(breaker: ServerErrorBreaker) -> ServerErrorBreaker:
    global _default
    _default = breaker
    return breaker


def assume_environment_not_tripped() -> None:
    """Abstain the calling test when the run's breaker has tripped."""
    default_breaker().assume_not_tripped()
