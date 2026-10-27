"""Fixed input labels checked against the code list the service actually serves.

A test that types a constant ("Budget", "Annual") into a coded field depends
on that label still existing. When the list changes, the field refuses the
input and the failure reads like a product defect. :func:`require_offered`
checks the constant against the served list first and raises a
:class:`~verification.evidence_verdict.HarnessError` naming the label and what
is offered, so the reader goes to the test, not the product.
"""

from __future__ import annotations

from collections.abc import Iterable

from verification.evidence_verdict import HarnessError


def require_offered(label: str, list_values: Iterable[str]) -> str:
    """Return ``label`` when the served code list offers it; raise :class:`HarnessError` if not.

    Compared exactly after trimming surrounding whitespace: a label that differs
    in case is a different label to the service.
    """
    offered = [str(v).strip() for v in list_values if v is not None]
    wanted = (label or "").strip()
    if not wanted:
        raise HarnessError("require_offered needs a non-blank label")
    if not offered:
        raise HarnessError(f"the served code list is empty, so {wanted!r} cannot be checked")
    if wanted not in offered:
        raise HarnessError(
            f"input label {wanted!r} is not in the served code list {offered}: the test's "
            "constant is stale, pick a label the service offers"
        )
    return wanted
