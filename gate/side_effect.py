"""A guard for the tests whose subject *is* a side effect: send, book, notify, charge.

Most of the gate keeps tests from writing where they should not (:mod:`gate.preflight`,
:mod:`gate.read_only`). Some checks cannot avoid it: the behaviour under test only
happens when an endpoint with external effects is called. Those calls get a guard that
makes every way of doing damage an explicit, visible refusal:

* **opt-in and allowlist** — :meth:`SideEffectGuard.check_env` skips unless the flag
  variable is truthy *and* the environment is allowed. A production name is refused even
  when someone lists it: a typo in a variable must not be the only thing between a test
  and a real customer;
* **one canonical id per call** — no separators, no whitespace, so ``"A-1,A-2"`` can never
  fan out into two effects;
* **a denylist** — protected ids, and any id that shares a resource with one (``related``),
  are refused;
* **a fresh precondition** — ``precheck`` judges a read taken right before the call, not
  the one the test made at collection time;
* **a cap** — ``min(max_calls, ceiling)``; the ceiling is the hard limit no configuration
  can raise;
* **abort on the first non-2xx** — every later call is refused with the reason, because a
  failing endpoint retried by twenty tests is twenty incidents.

Every call that is sent writes a ``before`` / ``answer`` / ``after`` JSON file and, when
the guard has a pytest ``request``, attaches it through :func:`results.evidence.attach`.

Run the effect once and let several tests judge it: :func:`side_effect_fixture` builds a
module-scoped fixture whose value is the :class:`SideEffectRun`::

    guard = lambda: SideEffectGuard("SEND_PROBE", allowed_envs={"qa"}, max_calls=1,
                                    ceiling=3, protected_ids={"SUB-000001"})
    sent = side_effect_fixture(guard, lambda g: g.call("SUB-000123", precheck, send,
                                                       read=read))

    def test_answered(sent): assert sent.outcome.status == 202
    def test_logged(sent): assert sent.outcome.after["outbox"]
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from core.logger import logger
from gate._env import env_bool

#: Names that are never a test environment, whatever the allowlist says.
PRODUCTION_NAMES = frozenset({"prod", "production", "prd", "live"})
#: One id: letters and digits, with ``-``, ``_`` or ``.`` inside it. No list separators.
CANONICAL_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
DEFAULT_EVIDENCE_DIR = Path("reports") / "side-effects"

Send = Callable[[str], "tuple[int, Any]"]
Precheck = Callable[[Any], "str | None"]
Read = Callable[[str], Any]


@dataclass(frozen=True)
class CallOutcome:
    """One attempted call. ``sent`` is False for every refusal, with ``refused`` saying why."""

    record_id: str
    sent: bool
    status: int | None = None
    answer: Any = None
    before: Any = None
    after: Any = None
    refused: str | None = None
    evidence: Path | None = None

    @property
    def succeeded(self) -> bool:
        return self.sent and self.status is not None and 200 <= self.status < 300

    def describe(self) -> str:
        if not self.sent:
            return f"{self.record_id}: NOT SENT - {self.refused}"
        return f"{self.record_id}: HTTP {self.status}"


@dataclass
class SideEffectGuard:
    flag_env: str
    allowed_envs: Collection[str]
    max_calls: int
    ceiling: int
    protected_ids: Collection[str] = ()
    related: Callable[[str], Iterable[str]] | None = None
    """Ids that share a resource with the given one; any protected one refuses the call."""
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR
    request: Any = None
    """A pytest ``request`` or item: every evidence file is attached to it."""
    environ: Mapping[str, str] | None = None
    calls: list[CallOutcome] = field(default_factory=list)
    aborted: str | None = None
    _env: str | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.ceiling < 1 or self.max_calls < 1:
            raise ValueError("max_calls and ceiling must both be at least 1")
        self.protected_ids = frozenset(self.protected_ids)

    @property
    def budget(self) -> int:
        """Calls this run may send: the configured cap, never above the hard ceiling."""
        return min(self.max_calls, self.ceiling)

    @property
    def sent(self) -> int:
        return sum(1 for c in self.calls if c.sent)

    def skip_reason(self, env: str) -> str | None:
        """Why this environment may not take side effects, or None when it may."""
        name = (env or "").strip().lower()
        if name in PRODUCTION_NAMES:
            return f"{env!r} is a production name: side effects are never sent there"
        if not env_bool(self.flag_env, False, self.environ):
            return f"side effects are opt-in: set {self.flag_env}=true to send them"
        allowed = {e.strip().lower() for e in self.allowed_envs}
        if name not in allowed:
            return f"{env!r} is not in the allowlist {sorted(allowed)}"
        return None

    def check_env(self, env: str) -> None:
        """Skip the test unless side effects are switched on and ``env`` is allowed."""
        reason = self.skip_reason(env)
        if reason is not None:
            pytest.skip(reason)
        self._env = env

    def call(
        self, record_id: str, precheck: Precheck, send: Send, *, read: Read | None = None
    ) -> CallOutcome:
        """Send one effect for ``record_id``, or refuse with the reason.

        ``read(record_id)`` is taken right before the call (``before``, handed to
        ``precheck``) and after it (``after``). ``precheck(before)`` returns a reason to
        refuse, or None. ``send(record_id)`` returns ``(status, answer)``.
        """
        with self._lock:
            refused = self._refusal(record_id)
            before = None
            if refused is None:
                try:
                    before = read(record_id) if read is not None else None
                    refused = precheck(before)
                except Exception as exc:  # noqa: BLE001 - an unreadable precondition is unmet
                    refused = f"precondition could not be read: {exc!r}"
                if refused is not None:
                    refused = f"precondition: {refused}"
            if refused is not None:
                outcome = CallOutcome(record_id, False, before=before, refused=refused)
                logger.warning("[side-effect] %s", outcome.describe())
                self.calls.append(outcome)
                return outcome
            try:
                status, answer = send(record_id)
            except Exception as exc:  # noqa: BLE001 - a transport error is a failed call
                status, answer = None, f"send raised: {exc!r}"
            after = None
            if read is not None:
                try:
                    after = read(record_id)
                except Exception as exc:  # noqa: BLE001 - the evidence says it was unreadable
                    after = {"unreadable": repr(exc)}
            path = self._write_evidence(record_id, before, status, answer, after)
            outcome = CallOutcome(record_id, True, status, answer, before, after, None, path)
            self.calls.append(outcome)
            logger.info("[side-effect] %s", outcome.describe())
            if not outcome.succeeded:
                self.aborted = f"{record_id} answered {status}: every later call is refused"
                logger.error("[side-effect] run aborted: %s", self.aborted)
            return outcome

    def _refusal(self, record_id: str) -> str | None:
        if self._env is None:
            return "check_env() has not passed for this guard"
        if self.aborted is not None:
            return f"run aborted: {self.aborted}"
        if not isinstance(record_id, str) or not CANONICAL_ID.fullmatch(record_id):
            return f"{record_id!r} is not exactly one canonical id (no separators, no whitespace)"
        if record_id in self.protected_ids:
            return f"{record_id} is protected"
        if self.related is not None:
            shared = sorted(set(self.related(record_id)) & set(self.protected_ids))
            if shared:
                return f"{record_id} shares a resource with protected {', '.join(shared)}"
        if self.sent >= self.budget:
            return f"call budget spent ({self.sent} of {self.budget}, ceiling {self.ceiling})"
        return None

    def _write_evidence(
        self, record_id: str, before: Any, status: int | None, answer: Any, after: Any
    ) -> Path:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = self.evidence_dir / f"{record_id}-{stamp}.json"
        document = {
            "env": self._env,
            "record_id": record_id,
            "before": before,
            "answer": {"status": status, "body": answer},
            "after": after,
        }
        path.write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")
        if self.request is not None:
            from results.evidence import attach

            attach(self.request, "other", path)
        return path


@dataclass(frozen=True)
class SideEffectRun:
    """What a module-scoped run produced; every test in the module reads the same one."""

    guard: SideEffectGuard
    result: Any

    @property
    def outcome(self) -> CallOutcome | None:
        """The last call, the common case of a run that sends one effect."""
        return self.guard.calls[-1] if self.guard.calls else None


def side_effect_fixture(
    make_guard: Callable[[], SideEffectGuard],
    execute: Callable[[SideEffectGuard], Any],
    *,
    env: Callable[[pytest.FixtureRequest], str] | None = None,
    name: str | None = None,
):
    """A module-scoped fixture: check the environment, run ``execute`` once, share the run.

    ``env(request)`` names the environment (default: ``--env``, else ``$TEST_ENV``).
    """

    @pytest.fixture(scope="module", name=name)
    def _run(request: pytest.FixtureRequest) -> SideEffectRun:
        guard = make_guard()
        guard.check_env(env(request) if env is not None else _default_env(request))
        return SideEffectRun(guard, execute(guard))

    return _run


def _default_env(request: pytest.FixtureRequest) -> str:
    try:
        chosen = request.config.getoption("--env")
    except ValueError:
        chosen = None
    return chosen or os.environ.get("TEST_ENV", "")


__all__ = [
    "CANONICAL_ID",
    "PRODUCTION_NAMES",
    "CallOutcome",
    "SideEffectGuard",
    "SideEffectRun",
    "side_effect_fixture",
]
