"""Document-vs-ledger reconciliation: what a document prints must match what was booked.

A generated document (invoice, credit note, statement) and the ledger entry
created by the same change are produced by different code paths, so they can
disagree while each looks right on its own. :func:`reconcile` reads the
labelled amounts off the document text (locale-aware, signs kept) and compares
them with the ledger legs the caller maps them to.

Findings are explicit about which side is missing something: a label the
document does not print, a leg the ledger does not carry, or two figures that
differ by more than ``tolerance``. ``compare_magnitudes`` covers documents that
print a refund as a positive "amount to be refunded" while the ledger books it
negative — the sign is then checked separately via ``expected_sign``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from oracles.amounts import amount_after_label


@dataclass(frozen=True)
class Mismatch:
    label: str
    kind: str  # "missing-on-document" | "missing-in-ledger" | "amount" | "sign"
    printed: Decimal | None = None
    booked: Decimal | None = None

    def __str__(self) -> str:
        return f"[{self.kind}] {self.label}: printed {self.printed}, booked {self.booked}"


@dataclass(frozen=True)
class Reconciliation:
    printed: Mapping[str, Decimal | None] = field(default_factory=dict)
    mismatches: tuple[Mismatch, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def describe(self) -> str:
        if self.ok:
            return f"document and ledger agree on {list(self.printed)}"
        return "document and ledger disagree:\n" + "\n".join(f"  {m}" for m in self.mismatches)


def printed_amounts(
    text: str, labels: list[str], locale: str = "nl_NL"
) -> dict[str, Decimal | None]:
    return {label: amount_after_label(text, label, locale) for label in labels}


def reconcile(
    document_text: str,
    ledger: Mapping[str, Decimal | None],
    label_to_leg: Mapping[str, str],
    *,
    locale: str = "nl_NL",
    tolerance: Decimal = Decimal("0.01"),
    compare_magnitudes: bool = False,
    expected_sign: int | None = None,
) -> Reconciliation:
    """Compare each printed label with its mapped ledger leg.

    ``expected_sign`` (-1 or 1) asserts the sign of every booked leg, which is
    what still catches a refund booked as a charge when magnitudes are compared.
    """
    printed = printed_amounts(document_text, list(label_to_leg), locale)
    out: list[Mismatch] = []
    for label, leg in label_to_leg.items():
        doc_value, booked = printed[label], ledger.get(leg)
        if doc_value is None:
            out.append(Mismatch(label, "missing-on-document", None, booked))
            continue
        if booked is None:
            out.append(Mismatch(label, "missing-in-ledger", doc_value, None))
            continue
        a, b = (abs(doc_value), abs(booked)) if compare_magnitudes else (doc_value, booked)
        if abs(a - b) > tolerance:
            out.append(Mismatch(label, "amount", doc_value, booked))
        if expected_sign is not None and booked != 0 and (booked > 0) != (expected_sign > 0):
            out.append(Mismatch(label, "sign", doc_value, booked))
    return Reconciliation(printed, tuple(out))
