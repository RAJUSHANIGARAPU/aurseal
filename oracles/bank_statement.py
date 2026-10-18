"""Parse an ISO 20022 camt.053 bank-to-customer statement into entries.

Each ``Ntry`` becomes a :class:`StatementEntry`: amount as :class:`Decimal`,
currency, direction (``CRDT`` money in, ``DBIT`` money out), booking date, the
end-to-end reference, the counterparty and the unstructured remittance text.

The counterparty is the *other* party: the creditor on a debit (we paid them),
the debtor on a credit (they paid us). Party names are read from both the
older ``Cdtr/Nm`` layout and the newer ``Cdtr/Pty/Nm`` one.

Parsing is namespace-agnostic: elements are matched by local name, so every
camt.053 version (``.001.02`` .. ``.001.13``) and any prefix style reads the
same. Documents with a DOCTYPE are refused: a statement never needs one, and
entity expansion is the classic XML attack surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

CREDIT = "CRDT"
DEBIT = "DBIT"


class StatementFormatError(ValueError):
    """The XML is not a readable camt.053 statement."""


@dataclass(frozen=True)
class StatementEntry:
    amount: Decimal
    currency: str
    direction: str
    booking_date: date | None
    reference: str | None
    counterparty: str | None
    remittance: str | None

    @property
    def signed_amount(self) -> Decimal:
        """Positive for money in, negative for money out."""
        return self.amount if self.direction == CREDIT else -self.amount


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _child(el: ET.Element | None, *path: str) -> ET.Element | None:
    for name in path:
        if el is None:
            return None
        el = next((c for c in el if _local(c.tag) == name), None)
    return el


def _text(el: ET.Element | None, *path: str) -> str | None:
    found = _child(el, *path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _iter(root: ET.Element, name: str):
    return (el for el in root.iter() if _local(el.tag) == name)


def _party_name(tx: ET.Element | None, role: str) -> str | None:
    parties = _child(tx, "RltdPties")
    return _text(parties, role, "Nm") or _text(parties, role, "Pty", "Nm")


def parse_camt053(xml: str | bytes) -> list[StatementEntry]:
    """Every entry of every statement in the document, in document order."""
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    if b"<!DOCTYPE" in raw.upper():
        raise StatementFormatError("statement XML with a DOCTYPE is refused")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise StatementFormatError(f"not well-formed XML: {exc}") from exc
    if not any(True for _ in _iter(root, "Stmt")):
        raise StatementFormatError(f"no Stmt element under <{_local(root.tag)}>")
    entries = []
    for ntry in _iter(root, "Ntry"):
        amt = _child(ntry, "Amt")
        if amt is None or not (amt.text or "").strip():
            raise StatementFormatError("entry without Amt")
        try:
            amount = Decimal(amt.text.strip())
        except InvalidOperation as exc:
            raise StatementFormatError(f"amount {amt.text!r} is not a number") from exc
        direction = (_text(ntry, "CdtDbtInd") or "").upper()
        if direction not in (CREDIT, DEBIT):
            raise StatementFormatError(f"entry {amount} has CdtDbtInd {direction!r}")
        tx = _child(ntry, "NtryDtls", "TxDtls")
        counterparty = _party_name(tx, "Cdtr" if direction == DEBIT else "Dbtr")
        booked = _text(ntry, "BookgDt", "Dt") or (_text(ntry, "BookgDt", "DtTm") or "")[:10]
        try:
            booking_date = date.fromisoformat(booked) if booked else None
        except ValueError:
            booking_date = None
        ustrd = [u.text.strip() for u in _iter(tx, "Ustrd") if u.text] if tx is not None else []
        entries.append(
            StatementEntry(
                amount=amount,
                currency=amt.get("Ccy", ""),
                direction=direction,
                booking_date=booking_date,
                reference=_text(tx, "Refs", "EndToEndId"),
                counterparty=counterparty,
                remittance=" ".join(ustrd) or None,
            )
        )
    return entries
