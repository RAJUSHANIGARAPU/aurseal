"""A single oracle whose result is interpreted according to whether the fix is deployed.

A fix reaches one environment before the others. Where it is expected (the
environment is listed in a config variable) the oracle is asserted positively
and a pass is release evidence (``proved``, plus a ``cleared`` detector
verdict so the defect's row agrees). On every other environment that oracle acts
as an expected-red detector, reporting ``reproduces`` until the fix is deployed
and ``cleared`` afterwards. Either way, an unmet control means no verdict is
given at all. Where the fix is expected and the oracle fails,
the test fails plainly and the defect is recorded as still reproducing::

    env = request.config.getoption("--env")
    judge(env, fix_scope_envs("ROUNDING_FIX_ENVS", default="staging"),
          "BUG-142", r"split totals differ by \\d+ cent",
          coverage=EvidenceVerdict("BUG-142 rounding"),
          control=lambda: assert_invoice_was_read(invoice),
          oracle=lambda: assert_totals_add_up(invoice),
          evidence=lambda: f"totals add up on {invoice.id}")

When the deployed version is known (measured, or stated by the operator through
:func:`core.build_info.stated_or_measured`), :func:`judge_by_version` switches on the
version instead of the environment name: at or above ``fixed_in`` the oracle is asserted,
below it the oracle runs as a detector. A stated version stamps every verdict text,
failure and skip with ``[version <x> stated, not measured]``; an unknown one abstains.

:func:`fingerprint_verdict` is the same switch for a build fingerprint
(:class:`core.build_info.DocumentFingerprint`): asserted where the release is expected,
written down as ``recorded`` everywhere else.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable

import pytest

from core.build_info import BuildInfo, FingerprintReading, Match, stamp, stamp_prefix
from verification.defect_detector import (
    Check,
    closed_defects,
    require_usable_signature,
    still_reproduces,
)
from verification.evidence_verdict import EvidenceVerdict
from verification.verdicts import Kind, RedVerdict, detail_prefix, record_verdict

logger = logging.getLogger(__name__)


def fix_scope_envs(variable: str, default: str = "") -> frozenset[str]:
    """Environment names in ``$variable`` (comma separated), else ``default``."""
    raw = os.environ.get(variable)
    return frozenset(e.strip() for e in (default if raw is None else raw).split(",") if e.strip())


def judge(
    env: str,
    fixed_on: frozenset[str] | set[str],
    ticket: str,
    signature: str,
    *,
    coverage: EvidenceVerdict,
    control: Check,
    oracle: Check,
    evidence: Callable[[], str],
) -> RedVerdict:
    """Assert ``oracle`` when ``env`` is in ``fixed_on``, else run it as a detector."""
    if env not in fixed_on:
        logger.info("[fix-scope] %s on %r: fix not expected, running as a detector", ticket, env)
        return still_reproduces(ticket, signature, oracle, exercised=control)
    pattern = require_usable_signature(ticket, signature)
    is_closed = ticket in closed_defects()
    logger.info("[fix-scope] %s on %r: fix expected, asserting it", ticket, env)
    try:
        control()
    except pytest.skip.Exception as abort:
        coverage.no_verdict(f"control skipped: {abort}")
    except Exception as exc:
        coverage.no_verdict(f"control failed, the mechanism was not exercised: {exc!r}")
    try:
        oracle()
    except AssertionError as failure:
        # The fix was expected here and is absent: a plain failure, recorded as the
        # defect's reading so its row does not claim the fix landed.
        message = str(failure)
        if not pattern.search(message):
            verdict = RedVerdict.SIGNATURE_MISMATCH
        else:
            verdict = RedVerdict.CLOSED_REGRESSED if is_closed else RedVerdict.REPRODUCES
        record_verdict(Kind.EXPECTED_RED, verdict, ticket=ticket, detail=message)
        raise
    proof = evidence()
    coverage.proved(proof)
    # The detector's reading too, so the defect's row agrees with the coverage row.
    verdict = RedVerdict.CLOSED_PASS if is_closed else RedVerdict.CLEARED
    record_verdict(
        Kind.EXPECTED_RED,
        verdict,
        ticket=ticket,
        detail=f"fix expected on {env!r} and asserted present: {proof}",
    )
    return verdict


# ---------------------------------------------------------------------------
# by version
# ---------------------------------------------------------------------------

_SEMVER = re.compile(
    r"v?(?P<nums>\d+(?:\.\d+){0,2})(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?"
)


def parse_version(text: str) -> tuple:
    """A sortable key for ``[v]MAJOR[.MINOR[.PATCH]][-pre][+build]``; malformed raises.

    A pre-release sorts before its release (``1.4.0-rc.1 < 1.4.0``); build metadata is
    ignored, as semantic versioning says.
    """
    match = _SEMVER.fullmatch((text or "").strip())
    if match is None:
        raise ValueError(f"not a semantic version: {text!r}")
    nums = [int(n) for n in match["nums"].split(".")]
    nums += [0] * (3 - len(nums))
    pre = match["pre"]
    if pre is None:
        return (*nums, 1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (*nums, 0, parts)


def by_version(build: BuildInfo, fixed_in: str) -> bool | None:
    """True when ``build`` is at or above ``fixed_in``; None when the version is unknown."""
    fixed = parse_version(fixed_in)
    if not build.known:
        return None
    return parse_version(build.value or "") >= fixed


def judge_by_version(
    build: BuildInfo,
    fixed_in: str,
    ticket: str,
    signature: str,
    *,
    coverage: EvidenceVerdict,
    control: Check,
    oracle: Check,
    evidence: Callable[[], str],
) -> RedVerdict:
    """:func:`judge`, switched on the deployed version instead of the environment name."""
    fixed = by_version(build, fixed_in)
    if fixed is None:
        coverage.no_verdict(
            f"{ticket}: deployed version unknown ({build.describe()}); state it or measure it"
        )
    label = f"version {build.value}"
    prefix = stamp_prefix(build)
    with detail_prefix(prefix):
        try:
            return judge(
                label,
                {label} if fixed else set(),
                ticket,
                signature,
                coverage=coverage,
                control=control,
                oracle=oracle,
                evidence=evidence,
            )
        except pytest.skip.Exception as skipped:
            if not prefix:
                raise
            pytest.skip(stamp(build, str(skipped)))
        except AssertionError as failure:
            if not prefix:
                raise
            raise AssertionError(stamp(build, str(failure))) from failure


def fingerprint_verdict(
    env: str,
    expected_on: frozenset[str] | set[str],
    reading: FingerprintReading,
    coverage: EvidenceVerdict,
) -> None:
    """Assert the fingerprint where the release is expected; record it everywhere else."""
    if env not in expected_on:
        coverage.recorded(f"build fingerprint on {env!r}: {reading.describe()}")
        return
    if reading.state is Match.UNREADABLE:
        coverage.no_verdict(f"build fingerprint unreadable on {env!r}: {reading.detail}")
    if not reading.matched:
        raise AssertionError(
            f"the release is expected on {env!r} but its fingerprint is absent: {reading.detail}"
        )
    coverage.proved(f"build fingerprint on {env!r}: {reading.describe()}")
