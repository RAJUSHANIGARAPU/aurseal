"""Run an expected-red detector against demo_sut with its fault switch on, or off.

A detector example is a pair: with the fault on it must report REPRODUCES, with the fault
off it must report CLEARED (``still_reproduces`` raises for that, by design). Both sides
go through :func:`verification.defect_detector.still_reproduces` with a positive control, and
the oracle's failure message starts with the active fault set, so the recorded verdict shows
which defect was switched on while it ran.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from verification.defect_detector import FIXED_TAG, still_reproduces
from verification.verdicts import RedVerdict, read_verdicts


def active_faults(control) -> list[str]:
    return list(control.client.state()["faults"])


def judge(
    control,
    *,
    ticket: str,
    signature: str,
    oracle: Callable[[], object],
    exercised: Callable[[], object],
    fault: str,
    fault_on: bool,
) -> dict:
    """Run the detector and assert the side of the pair; returns the recorded verdict."""
    faults = active_faults(control)
    assert (fault in faults) is fault_on, f"expected {fault} on={fault_on}, demo reports {faults}"
    evidence = ",".join(faults) or "none"

    def oracle_with_evidence() -> None:
        try:
            oracle()
        except AssertionError as failure:
            raise AssertionError(f"[demo faults: {evidence}] {failure}") from failure

    if fault_on:
        verdict = still_reproduces(ticket, signature, oracle_with_evidence, exercised=exercised)
        assert verdict is RedVerdict.REPRODUCES
    else:
        with pytest.raises(AssertionError, match=FIXED_TAG):
            still_reproduces(ticket, signature, oracle_with_evidence, exercised=exercised)
    recorded = [r for r in read_verdicts() if r["ticket"] == ticket]
    assert recorded, f"no verdict was recorded for {ticket}"
    last = recorded[-1]
    if fault_on:
        assert last["verdict"] == RedVerdict.REPRODUCES.value
        assert fault in last["detail"], f"the verdict must name the active fault: {last}"
    else:
        assert last["verdict"] == RedVerdict.CLEARED.value
        assert control.evidence() == "none", "the healthy side must run with no fault on"
    return last
