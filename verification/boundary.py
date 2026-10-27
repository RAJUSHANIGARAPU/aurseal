"""Boundary sibling control: prove the validator ran before crediting an acceptance.

"The boundary value was accepted" is evidence only if the same validator would
have refused the value next to it. A draft whose validation never fires
accepts the boundary value too, and a test that submits only the boundary
cannot tell the two apart.

:func:`assert_boundary` therefore runs the pair in a fixed order on the same
draft (or the same endpoint):

1. ``refuse()`` submits the adjacent invalid value. It must come back with a
   refusal whose text matches one of ``refusal_matches``. Anything else (no
   refusal, or a refusal by some other rule) means the control did not run the
   validator under test, so the check abstains through ``no_verdict``: nothing
   about the boundary is known.
2. ``accept()`` submits the boundary value and must succeed.

Both callables may return an :class:`~api.clients.http_engine.ApiResponse`
(a non-2xx answer is a refusal whose text is the body), a plain refusal text or
``None`` (``refuse`` only: no refusal), or a bool (``accept`` only). A
``submit_and_capture`` callable from the UI layer fits by returning the
message it captured.

The verdict text quotes both values and the refusal, so one line is enough to
judge the run::

    assert_boundary(
        accept=lambda: api.post("/plans", json={**base, "seats": 500}),
        refuse=lambda: api.post("/plans", json={**base, "seats": 501}),
        refusal_matches=["at most 500 seats"],
        accepted_value=500,
        refused_value=501,
    )
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from verification.evidence_verdict import EvidenceVerdict

Matcher = str | re.Pattern[str]


def refusal_text(outcome: Any) -> str | None:
    """The refusal an outcome carries, or ``None`` when it is not a refusal."""
    if outcome is None or isinstance(outcome, bool):
        return None
    if isinstance(outcome, str):
        return outcome.strip() or None
    status = getattr(outcome, "status", None)
    if isinstance(status, int):
        if 200 <= status < 300:
            return None
        text = getattr(outcome, "text", "")
        return f"HTTP {status} {text}".strip()
    raise TypeError(f"cannot read a refusal from {type(outcome).__name__}: {outcome!r}")


def _accepted(outcome: Any) -> bool:
    if isinstance(outcome, bool):
        return outcome
    if outcome is None:
        return True
    status = getattr(outcome, "status", None)
    if isinstance(status, int):
        return 200 <= status < 300
    return True


def matches(text: str, patterns: Iterable[Matcher]) -> Matcher | None:
    """The first pattern ``text`` matches: a substring, or a regex searched in it."""
    for pattern in patterns:
        if isinstance(pattern, re.Pattern):
            if pattern.search(text):
                return pattern
        elif pattern and pattern in text:
            return pattern
    return None


def assert_boundary(
    accept: Callable[[], Any],
    refuse: Callable[[], Any],
    *,
    refusal_matches: Iterable[Matcher],
    accepted_value: Any = "<boundary>",
    refused_value: Any = "<sibling>",
    subject: str = "boundary",
    verdict: EvidenceVerdict | None = None,
) -> str:
    """Run the refusal control, then the acceptance; return (and record) the evidence."""
    patterns = list(refusal_matches)
    if not patterns:
        raise ValueError("a boundary control needs at least one refusal text to match")
    coverage = verdict or EvidenceVerdict(subject)

    refusal = refusal_text(refuse())
    if refusal is None:
        coverage.no_verdict(
            f"boundary control did not refuse: {refused_value!r} was accepted, so the "
            "validator under test may never run"
        )
    if matches(refusal, patterns) is None:
        coverage.no_verdict(
            f"boundary control did not refuse with the expected rule: {refused_value!r} was "
            f"refused with {refusal!r}, none of {[str(p) for p in patterns]}"
        )

    outcome = accept()
    if not _accepted(outcome):
        raise AssertionError(
            f"{subject}: boundary value {accepted_value!r} was refused "
            f"({refusal_text(outcome)!r}) although its sibling {refused_value!r} was refused "
            f"with {refusal!r}: the boundary is not where the rule puts it"
        )
    evidence = (
        f"{accepted_value!r} accepted; its sibling {refused_value!r} refused with {refusal!r}"
    )
    coverage.proved(evidence)
    return evidence
