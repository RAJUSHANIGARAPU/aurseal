"""Skip or fail: what "this could not run" means depends on why the run exists.

In the default gate an environment problem is a skip: a login timeout must not
read as "the feature is broken". But a lane whose *whole purpose* is to run a
suite — or to produce a verdict — that skips everything exits 0 with
``0 passed`` and is indistinguishable from "everything is fine". There, not
running is a failure.

Each lane opts in through its own variable, so one lane requiring execution
never silently arms another. The decision lives here once instead of being
re-implemented per suite.

:func:`resolve_checkout` applies the same policy to tests that bind to an
external checkout (a configuration repository read at a pinned ref): absent
checkout means abstain, unless the lane sets the ``required`` variable.

"Out of scope here" is a third answer, apart from both. A test written for one
environment has nothing to say about another, and a check that can never reach a
verdict on an environment says so once, with a pointer to where the behaviour *is*
covered. :func:`not_applicable` always skips, whatever the required switches say,
and records a ``not_applicable`` coverage verdict; the run-trust gate lists those
apart instead of counting them as abstentions. :func:`runs_only_on` (and the
``only_env`` marker applied by ``gate.pytest_plugin``) is built on it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import pytest

from gate._env import env_bool
from gate.verdicts import NOT_APPLICABLE_PREFIX

SUITE_REQUIRED_ENV = "SUITE_REQUIRED"
VERDICT_REQUIRED_ENV = "VERDICT_REQUIRED"


@dataclass(frozen=True)
class Requirement:
    """One lane's skip-or-fail switch, read from ``env_var`` at call time."""

    env_var: str
    what: str = "this suite"

    def required(self) -> bool:
        return env_bool(self.env_var, False)

    def require_or_skip(self, usable: bool, reason: str, *, strict: bool | None = None) -> None:
        """Return when ``usable``; otherwise skip — or fail when the lane requires it."""
        if usable:
            return
        self.abstain(reason, strict=strict)

    def abstain(
        self, reason: str, *, subject: str | None = None, strict: bool | None = None
    ) -> NoReturn:
        """Never returns: skips, or fails when the lane requires an answer."""
        strict = self.required() if strict is None else strict
        label = f"{subject} - " if subject else ""
        if strict:
            pytest.fail(
                f"{label}{self.what} could not run, and this lane requires it: {reason}. "
                "A run that verified nothing must not report success - fix the environment or "
                f"the lane rather than lowering the bar ({self.env_var}=false).",
                pytrace=False,
            )
        pytest.skip(f"{label}{reason}")


SUITE = Requirement(SUITE_REQUIRED_ENV, "this suite")
VERDICT = Requirement(VERDICT_REQUIRED_ENV, "a verdict")


def require_or_skip(usable: bool, reason: str) -> None:
    SUITE.require_or_skip(usable, reason)


def require_verdict_or_skip(subject: str, reason: str) -> NoReturn:
    VERDICT.abstain(reason, subject=subject)


# ---------------------------------------------------------------- out of scope


def not_applicable(reason: str, *, covered_by: str, subject: str | None = None) -> NoReturn:
    """Always skip: out of scope here is not "could not run", so no required switch applies.

    ``covered_by`` is mandatory and names where the behaviour is covered instead (another
    environment, another test, a manual check). A ``not_applicable`` coverage verdict is
    recorded for the run-trust gate and the results store.
    """
    if not str(covered_by or "").strip():
        raise ValueError("not_applicable needs covered_by: say where this behaviour is covered")
    from verification.verdicts import CoverageOutcome, Kind, record_verdict

    record_verdict(
        Kind.COVERAGE,
        CoverageOutcome.NOT_APPLICABLE,
        subject=subject,
        detail=f"{reason}; covered by {covered_by}",
    )
    pytest.skip(f"{NOT_APPLICABLE_PREFIX}{reason} (covered by {covered_by})")


def active_env(env: str | None = None) -> str:
    """The environment a check runs against: ``env``, else ``$TEST_ENV``, else ``qa``."""
    from core.config_reader import resolve_env

    return resolve_env(env)


def runs_only_on(
    envs: Iterable[str],
    what: str = "this test",
    env: str | None = None,
    *,
    covered_by: str | None = None,
) -> None:
    """Return on an environment in ``envs``; anywhere else, skip as not applicable.

    Deliberately not :func:`require_verdict_or_skip`: another environment is out of scope,
    not an unmet precondition, so a lane that requires verdicts still skips it.
    """
    allowed = sorted({e.strip() for e in envs if e and e.strip()})
    if not allowed:
        raise ValueError("runs_only_on needs at least one environment")
    current = active_env(env)
    if current in allowed:
        return
    not_applicable(
        f"{what} only runs on {allowed}, not on {current!r}",
        covered_by=covered_by or f"the same check on {', '.join(allowed)}",
    )


# ---------------------------------------------------------------- external checkout


@dataclass(frozen=True)
class CheckoutResolution:
    path: Path | None
    usable: bool
    diagnostic: str


def resolve_checkout(
    path_env: str,
    *,
    expected: tuple[str, ...] = (),
    fallback: str | Path | None = None,
) -> CheckoutResolution:
    """Locate an external checkout; never raises.

    The diagnostic distinguishes the cases that need different fixes: the
    setting is absent, it is malformed, it points at a directory that does not
    exist, or the checkout is half-populated (``expected`` entries missing).
    """
    raw = os.environ.get(path_env, "").strip()
    source = f"${path_env}"
    if not raw:
        if fallback is None:
            return CheckoutResolution(None, False, f"{source} is not set and there is no fallback")
        raw, source = str(fallback), f"fallback {fallback}"
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return CheckoutResolution(None, False, f"{source} is not a usable path ({raw!r}): {exc}")
    if not path.is_dir():
        return CheckoutResolution(path, False, f"{source} -> {path} does not exist")
    missing = [entry for entry in expected if not (path / entry).exists()]
    if missing:
        return CheckoutResolution(
            path, False, f"{source} -> {path} is half-populated: missing {', '.join(missing)}"
        )
    return CheckoutResolution(path, True, f"{source} -> {path}")


def require_checkout(
    path_env: str,
    required_env: str,
    *,
    expected: tuple[str, ...] = (),
    fallback: str | Path | None = None,
) -> Path:
    """The checkout path, or abstain (skip) — fail instead when ``required_env`` is true."""
    resolution = resolve_checkout(path_env, expected=expected, fallback=fallback)
    if not resolution.usable:
        Requirement(required_env, "binding to the external checkout").abstain(resolution.diagnostic)
    assert resolution.path is not None
    return resolution.path
