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
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

import pytest

from verification.defect_detector import (
    Check,
    closed_defects,
    require_usable_signature,
    still_reproduces,
)
from verification.evidence_verdict import EvidenceVerdict
from verification.verdicts import Kind, RedVerdict, record_verdict

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
