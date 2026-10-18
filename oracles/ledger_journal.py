"""Read a general-ledger journal for one transaction, without mistaking silence for proof.

The question "did this transaction book anything on account X?" is easy to answer
wrongly: filter the journal for the transaction, sum the lines on X, get zero, call
it proved. Zero is also what comes back when the account mapping was misread, when
the references never matched an entry, or when the journal read returned nothing.
So this module splits the read into steps that can each fail by name:

1. :func:`account_for_mapping` - the ledger account mapped to a role (``FEES``),
   from the service's own mapping; an unknown role raises instead of guessing;
2. :func:`references_of` - the reference ids a transaction carries;
3. :func:`entries_referencing` / :func:`lines_for_references` - journal entries
   that reference them (on the entry or on a line), and their non-zero lines on
   the account;
4. :func:`claim_absent_on_account` - "the account carries no lines for these
   references" is only allowed once the same account is shown to carry lines
   *elsewhere* in the journal that was read. Otherwise it raises
   :class:`NotMeasured`: the instrument could not have seen a line, so its
   silence proves nothing.

Field names differ per ledger; :class:`JournalShape` names them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

Entry = Mapping[str, Any]


class NotMeasured(AssertionError):
    """An absence claim was made by an instrument that could not have seen the thing."""


@dataclass(frozen=True)
class JournalShape:
    """Where a journal keeps its references, lines, accounts and amounts."""

    entry_references: tuple[str, ...] = ("references", "reference")
    line_references: tuple[str, ...] = ("references",)
    lines: str = "lines"
    account: str = "account"
    debit: str = "debit"
    credit: str = "credit"


DEFAULT_SHAPE = JournalShape()


def _ids(value: Any) -> set[str]:
    if value is None or value == "":
        return set()
    if isinstance(value, list | tuple | set):
        return {str(v) for v in value if v not in (None, "")}
    return {str(value)}


def account_for_mapping(
    mappings: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    role: str,
    *,
    role_key: str = "role",
    account_key: str = "account",
) -> str:
    """The account mapped to ``role``. Accepts ``{role: account}`` or ``[{role, account}]``."""
    if isinstance(mappings, Mapping):
        found = mappings.get(role)
    else:
        found = next((m.get(account_key) for m in mappings if m.get(role_key) == role), None)
    if found in (None, ""):
        roles = (
            sorted(mappings)
            if isinstance(mappings, Mapping)
            else sorted(str(m.get(role_key)) for m in mappings)
        )
        raise LookupError(f"no ledger account is mapped to role {role!r} (mapped: {roles})")
    return str(found)


def references_of(transaction: Mapping[str, Any], keys: Iterable[str] = ("reference",)) -> set[str]:
    """Reference ids a transaction carries under ``keys`` (scalars or lists)."""
    refs: set[str] = set()
    for key in keys:
        refs |= _ids(transaction.get(key))
    return refs


def _line_amount(line: Mapping[str, Any], shape: JournalShape) -> Decimal:
    debit = Decimal(str(line.get(shape.debit) or 0))
    credit = Decimal(str(line.get(shape.credit) or 0))
    return debit - credit


def entries_referencing(
    entries: Iterable[Entry], references: Iterable[str], shape: JournalShape = DEFAULT_SHAPE
) -> list[Entry]:
    """Entries whose own references, or any line's references, include one of ``references``."""
    wanted = {str(r) for r in references}
    out = []
    for entry in entries:
        refs: set[str] = set()
        for key in shape.entry_references:
            refs |= _ids(entry.get(key))
        for line in entry.get(shape.lines) or ():
            for key in shape.line_references:
                refs |= _ids(line.get(key))
        if refs & wanted:
            out.append(entry)
    return out


def lines_on_account(
    entries: Iterable[Entry], account: str, shape: JournalShape = DEFAULT_SHAPE
) -> list[Mapping[str, Any]]:
    """Non-zero lines booked on ``account`` across ``entries``."""
    return [
        line
        for entry in entries
        for line in entry.get(shape.lines) or ()
        if str(line.get(shape.account)) == str(account) and _line_amount(line, shape) != 0
    ]


def lines_for_references(
    entries: Iterable[Entry],
    references: Iterable[str],
    account: str,
    shape: JournalShape = DEFAULT_SHAPE,
) -> list[Mapping[str, Any]]:
    """Non-zero lines on ``account`` in the entries that reference ``references``."""
    return lines_on_account(entries_referencing(entries, references, shape), account, shape)


def booked_amount(
    lines: Iterable[Mapping[str, Any]], shape: JournalShape = DEFAULT_SHAPE
) -> Decimal:
    """Net amount of ``lines`` (debit minus credit)."""
    return sum((_line_amount(line, shape) for line in lines), Decimal(0))


def claim_absent_on_account(
    entries: Sequence[Entry],
    references: Iterable[str],
    account: str,
    shape: JournalShape = DEFAULT_SHAPE,
) -> None:
    """Assert ``account`` carries no lines for ``references`` - only when that is measurable.

    Raises :class:`NotMeasured` when there are no references, or when the account carries
    no lines anywhere else in ``entries`` (then no line could have been seen for these
    references either). Raises ``AssertionError`` naming the lines when some exist.
    """
    refs = {str(r) for r in references}
    if not refs:
        raise NotMeasured(f"no reference ids to look for on account {account}")
    mine = entries_referencing(entries, refs, shape)
    others = [e for e in entries if e not in mine]
    if not lines_on_account(others, account, shape):
        raise NotMeasured(
            f"account {account} carries no lines anywhere else in the {len(entries)} journal "
            f"entr(y/ies) read, so 'no lines for {sorted(refs)}' would be an absence of "
            f"measurement, not a finding"
        )
    found = lines_on_account(mine, account, shape)
    if found:
        raise AssertionError(
            f"account {account} carries {len(found)} line(s) worth {booked_amount(found, shape)} "
            f"for {sorted(refs)}: {list(found)}"
        )


def describe(entries: Iterable[Entry], shape: JournalShape = DEFAULT_SHAPE) -> str:
    """One line per entry: references and ``account:amount`` per line."""
    rows = []
    for entry in entries:
        refs: set[str] = set()
        for key in shape.entry_references:
            refs |= _ids(entry.get(key))
        lines = ", ".join(
            f"{line.get(shape.account)}:{_line_amount(line, shape)}"
            for line in entry.get(shape.lines) or ()
        )
        rows.append(f"{sorted(refs)} -> {lines or 'no lines'}")
    return "\n".join(rows) or "no journal entries"
