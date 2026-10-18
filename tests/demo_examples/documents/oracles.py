"""Content oracles for the demo's subscription documents.

Several documents end with a ``Details`` section (data region, workspace, billing contact,
PO reference). The oracles here read a labelled line **inside that section**: the same words
printed in a heading or another block do not count.

Each oracle separates the failure it exists for from its near misses, which fail with
different wording, so a layout drift or a wrong value is never counted as the tracked
problem:

* :func:`assert_line_absent` - a conditional line whose value is missing must leave no line
  at all; a label printed with ``None`` or an empty value is the failure.
* :func:`assert_line_value` - a labelled line carries the expected value; a missing line and
  a wrong value are reported differently.
* :func:`assert_money_line` - a money line is printed as ``EUR <amount>`` with two decimals;
  a bare number, or the right format with the wrong amount, are told apart.

:func:`assert_document_exercised` is the positive control the detectors pair with them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from oracles import amounts

DETAILS = "Details"
CONTACT_LABEL = "Billing contact:"
CHARGE_LABEL = "Charge per period:"

CONTACT_DEFECT = "DEMO-116"
CONTACT_SIGNATURE = rf"{CONTACT_DEFECT}: a billing contact line is printed without a contact"
AMOUNT_DEFECT = "DEMO-117"
AMOUNT_SIGNATURE = rf"{AMOUNT_DEFECT}: the charge per period is not printed as money"
_MONEY = re.compile(r"EUR\s?(-?\d+(?:\.\d{1,2})?)")
_FORMATTED = re.compile(r"^EUR -?\d+\.\d{2}$")


def non_blank_lines(text: str | None) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def details_section(text: str | None) -> list[str]:
    """Non-blank lines after the *Details* heading ([] when there is no such heading)."""
    lines = non_blank_lines(text)
    if DETAILS not in lines:
        return []
    return lines[lines.index(DETAILS) + 1 :]


def labelled_lines(lines: list[str], label: str) -> list[str]:
    """The values printed after ``label`` on lines that start with it."""
    return [line[len(label) :].strip() for line in lines if line.startswith(label)]


def assert_line_absent(text: str | None, label: str, *, section: bool = True) -> None:
    """A conditional line with nothing to show must not be printed at all."""
    lines = details_section(text) if section else non_blank_lines(text)
    values = labelled_lines(lines, label)
    if not values:
        return
    empty = [v for v in values if v in ("", "None", "null", "-")]
    if empty:
        shown = [f"{label} {v}" for v in empty]
        raise AssertionError(f"the '{label}' line has no value but is printed anyway: {shown}")
    raise AssertionError(f"the '{label}' line should be absent but shows {values}")


def assert_line_value(text: str | None, label: str, expected: str, *, section: bool = True) -> None:
    """``label`` is printed with ``expected`` on one line."""
    lines = details_section(text) if section else non_blank_lines(text)
    values = labelled_lines(lines, label)
    if expected in values:
        return
    if values:
        raise AssertionError(f"'{label}' shows {values}, not {expected!r}: a wrong value")
    raise AssertionError(f"no '{label}' line where one was expected; lines: {lines}")


def assert_money_line(text: str | None, label: str, expected: Decimal | None = None) -> None:
    """``label`` is followed by a formatted money amount (``EUR 12.30``)."""
    values = labelled_lines(non_blank_lines(text), label)
    if not values:
        raise AssertionError(f"no '{label}' line on the document")
    value = values[0]
    if not _FORMATTED.match(value):
        raise AssertionError(f"'{label}' is not printed as money: {value!r} (expected 'EUR 0.00')")
    if expected is not None:
        printed = amounts.parse_amount(value[4:], LOCALE)
        if printed != expected:
            raise AssertionError(f"'{label}' is formatted but shows {printed}, not {expected}")


def assert_contact_line_absent(text: str | None) -> None:
    """Tracked: without a billing contact the account-opened letter prints no contact line."""
    try:
        assert_line_absent(text, CONTACT_LABEL)
    except AssertionError as failure:
        if "no value but is printed anyway" in str(failure):
            raise AssertionError(
                f"{CONTACT_DEFECT}: a billing contact line is printed without a contact ({failure})"
            ) from failure
        raise


def assert_period_charge_formatted(text: str | None, expected: Decimal | None = None) -> None:
    """Tracked: the charge per period is printed as a money amount."""
    try:
        assert_money_line(text, CHARGE_LABEL, expected)
    except AssertionError as failure:
        if "not printed as money" in str(failure):
            raise AssertionError(
                f"{AMOUNT_DEFECT}: the charge per period is not printed as money ({failure})"
            ) from failure
        raise


def assert_document_exercised(text: str | None, subscription_number: str, anchor: str) -> None:
    """Positive control: the text is this subscription's document and has its details.

    An empty extraction, another subscription's document or a template without the section
    produce the same absence an absence check reads as "fine"; this rules them out.
    """
    lines = non_blank_lines(text)
    assert lines, "the document text is empty"
    assert any(subscription_number in line for line in lines), (
        f"{subscription_number} is not on the document: {lines[:3]}"
    )
    section = details_section(text)
    assert section, f"no {DETAILS} section on the document of {subscription_number}"
    assert any(anchor in line for line in section), f"{anchor!r} not in {DETAILS}: {section}"


def assert_all(checks: Mapping[str, Callable[[], object]]) -> None:
    """Run every check and report all failures together, one line per finding."""
    failures = []
    for name, check in checks.items():
        try:
            check()
        except AssertionError as failure:
            failures.append(f"{name}: {failure}")
    if failures:
        raise AssertionError("\n".join(failures))


# -- money on a document ----------------------------------------------------------------------

#: The demo prints money as ``EUR 1234.56`` (dot decimal, no grouping), i.e. ``en_GB`` digits.
LOCALE = "en_GB"


def money_amounts(text: str | None) -> list[Decimal]:
    """Every currency-marked amount in reading order (a bare number such as an id is not one)."""
    return [amounts.parse_amount(m.group(1), LOCALE) for m in _MONEY.finditer(text or "")]


def money_after_label(text: str | None, label: str) -> Decimal | None:
    """The currency-marked amount on the first line carrying ``label`` (sign kept)."""
    for line in (text or "").splitlines():
        if label in line:
            found = money_amounts(line[line.index(label) + len(label) :])
            if found:
                return found[0]
    return None


def numeric_leaves(node: Any, path: str = "") -> list[tuple[str, Decimal]]:
    """Every numeric leaf of a JSON value as ``(dotted path, amount)`` (booleans excluded)."""
    if isinstance(node, bool):
        return []
    if isinstance(node, int | float):
        return [(path, Decimal(str(node)))]
    if isinstance(node, Mapping):
        return [
            leaf for k, v in node.items() for leaf in numeric_leaves(v, f"{path}.{k}".strip("."))
        ]
    if isinstance(node, list):
        return [leaf for i, v in enumerate(node) for leaf in numeric_leaves(v, f"{path}.{i}")]
    return []


def matching_leaf(response: Any, amount: Decimal, tolerance: Decimal = Decimal("0.02")) -> str:
    """The path of a numeric leaf of ``response`` within ``tolerance`` of ``amount``.

    Fails naming the closest leaf when none matches. "Any leaf" can pass on a coincidental
    match, so callers choose request values whose leaves are all distinct.
    """
    leaves = numeric_leaves(response)
    for path, value in leaves:
        if abs(value - amount) <= tolerance:
            return path
    best = min(leaves, key=lambda leaf: abs(leaf[1] - amount), default=None)
    raise AssertionError(
        f"the document amount {amount} matches no numeric field of the price response "
        f"(closest: {best[0] if best else '-'} = {best[1] if best else '-'})"
    )
