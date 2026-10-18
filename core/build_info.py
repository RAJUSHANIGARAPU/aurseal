"""
Which build is the environment under test actually running? Measured, once per run.

A failure message that names a cause ("the environment is still on the old build")
without the run having asked the environment is worse than no diagnosis: it ends
the investigation instead of starting one, and every later reader who searches the
log for that version finds the claim confirming itself.

``BuildInfoProbe`` asks a list of candidate endpoints in order and returns
the first real answer. *Absent is a value*: when nothing answers, the result is an
explicit ``UNKNOWN`` that carries why each candidate missed (404, 403, not JSON,
JSON without the field, unreachable). It never raises and never invents a version.

``observe_once`` caches the answer for the process, logs it once, and
``last_observed`` hands it to diagnostics without ever making an HTTP call — a
message composed before any probe ran says "not measured", which sends a reader to
the harness rather than the environment.

Transport is injected (``fetch(path) -> (status, body)``), so every branch is
testable offline and any HTTP client can be plugged in. Candidates are injectable
too; the defaults cover a Spring Boot ``/actuator/info`` and a generic
``/version`` endpoint.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core.logger import logger

Fetch = Callable[[str], "tuple[int, str] | None"]
Reader = Callable[[object], "BuildInfo | None"]


@dataclass(frozen=True)
class BuildInfo:
    source: str | None = None
    value: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        for name in ("source", "value", "detail"):
            raw = getattr(self, name)
            if raw is not None and not str(raw).strip():
                object.__setattr__(self, name, None)

    @classmethod
    def measured(cls, source: str, value: str, detail: str | None = None) -> BuildInfo:
        return cls(source, value, detail)

    @classmethod
    def unknown(cls, reason: str) -> BuildInfo:
        return cls(None, None, reason)

    @classmethod
    def not_measured(cls) -> BuildInfo:
        return cls.unknown("no version probe has run in this process")

    @property
    def known(self) -> bool:
        return self.value is not None

    def describe(self) -> str:
        """One line that can never be misread as a claim it does not support."""
        if not self.known:
            return f"UNKNOWN — {self.detail or 'no version endpoint answered'}"
        source = self.source or "unnamed source"
        return f"{self.value} [{source}]" + (f" — {self.detail}" if self.detail else "")


def _text(node: object, *path: str) -> str | None:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, (str, int, float)) and not isinstance(node, bool):
        text = str(node).strip()
        return text or None
    return None


def read_actuator_info(body: object) -> BuildInfo | None:
    """``build.version`` from a Spring Boot ``/actuator/info``; git commit as detail."""
    version = _text(body, "build", "version")
    if version is None:
        return None
    parts = [f"service {_text(body, 'build', 'name') or '(unnamed)'}"]
    built = _text(body, "build", "time")
    if built:
        parts.append(f"built {built}")
    commit = _text(body, "git", "commit", "id") or _text(body, "git", "commit")
    if commit:
        parts.append(f"commit {commit}")
    return BuildInfo.measured("build endpoint (GET /actuator/info)", version, ", ".join(parts))


def read_version_field(body: object) -> BuildInfo | None:
    """A top-level ``version`` (or ``versionTag``) field, as many ``/version`` endpoints return."""
    version = _text(body, "version") or _text(body, "versionTag")
    if version is None:
        return None
    detail = ", ".join(
        f"{key} {value}" for key in ("name", "commit", "createdAt") if (value := _text(body, key))
    )
    return BuildInfo.measured("version endpoint (GET /version)", version, detail or None)


@dataclass(frozen=True)
class Candidate:
    path: str
    read: Reader


DEFAULT_CANDIDATES: tuple[Candidate, ...] = (
    Candidate("/version", read_version_field),
    Candidate("/actuator/info", read_actuator_info),
)


def _short(message: str) -> str:
    one_line = " ".join(message.split())
    return one_line if len(one_line) <= 160 else one_line[:160] + "…"


class BuildInfoProbe:
    def __init__(self, fetch: Fetch, candidates: Sequence[Candidate] = DEFAULT_CANDIDATES) -> None:
        self._fetch = fetch
        self._candidates = tuple(candidates)

    def probe(self) -> BuildInfo:
        misses: list[str] = []
        for candidate in self._candidates:
            version, miss = self._attempt(candidate)
            if version is not None:
                return version
            logger.debug("[version] %s", miss)
            misses.append(miss)
        return BuildInfo.unknown("; ".join(misses) or "no candidates configured")

    def _attempt(self, candidate: Candidate) -> tuple[BuildInfo | None, str]:
        path = candidate.path
        try:
            response = self._fetch(path)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a reason
            return (
                None,
                f"{path} -> unreachable ({type(exc).__name__}: {_short(str(exc)) or 'no message'})",
            )
        if response is None:
            return None, f"{path} -> no response"
        status, body = response
        if status != 200:
            return None, f"{path} -> HTTP {status}"
        try:
            parsed = json.loads(body) if body and body.strip() else None
        except ValueError:
            parsed = None
        if parsed is None:
            return None, f"{path} -> HTTP 200 with an unparseable body"
        version = candidate.read(parsed)
        if version is None or not version.known:
            return None, f"{path} -> HTTP 200 but no version field in the body"
        return version, ""


_observed: BuildInfo | None = None
_lock = threading.Lock()


def observe_once(probe: BuildInfoProbe) -> BuildInfo:
    """Measure on the first call in this process, log it, return the same answer after."""
    global _observed
    with _lock:
        if _observed is None:
            _observed = probe.probe()
            logger.info("[version] deployed version: %s", _observed.describe())
            if not _observed.known:
                logger.info(
                    "[version] build version unknown; whatever these diagnostics say about the "
                    "deployed build is a guess, not a measurement"
                )
        return _observed


def last_observed() -> BuildInfo:
    """The measurement taken earlier in this process, or ``not_measured``. Never probes."""
    return _observed if _observed is not None else BuildInfo.not_measured()


def reset_for_tests() -> None:
    global _observed
    with _lock:
        _observed = None


def requests_fetch(base_url: str, session=None, timeout_s: float = 5.0, headers=None) -> Fetch:
    """A ``Fetch`` over ``requests`` against ``base_url`` (imported lazily)."""
    import requests

    client = session or requests.Session()

    def fetch(path: str) -> tuple[int, str]:
        response = client.get(base_url.rstrip("/") + path, timeout=timeout_s, headers=headers)
        return response.status_code, response.text

    return fetch
