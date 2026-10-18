"""Pure decisions the change examples share. No demo calls here: unit-tested offline.

**Baseline gaps.** A subscription's holder block is read from the customer record, so its
fields are *derived*. When such a field is already blank before a change, a later "the change
blanked it" check would blame the change for an old gap. :func:`blank_derived_fields` finds
the blanks (``None``, empty or whitespace-only strings, empty containers), and
:func:`classify_baseline_gap` decides what a blank baseline means:

=========================================  ==========  ===============================
baseline                                   built here  verdict
=========================================  ==========  ===============================
no blank derived field                     either      ``NONE``: compare every field
some blank                                 yes         ``FAIL``: our own seed is broken
some blank                                 no          ``EXCLUDE`` the blank fields
every derived field blank                  no          ``ABSTAIN``: nothing to compare
=========================================  ==========  ===============================

**Rejection attribution.** :data:`START_CHANGE_ENVIRONMENT` / :data:`START_CHANGE_DEFECT` are
the pattern sets the start-date detector hands to
:func:`verification.defect_detector.attribute_failure`, and :func:`rejection_text` flattens a
rejected response into the one line they are matched against. Environment patterns win.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any

#: Holder fields a subscription takes from its customer record.
DERIVED_FIELDS: tuple[str, ...] = ("name", "type", "email", "phone")


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (Mapping, list, tuple, set)):
        return len(value) == 0
    return False


def blank_derived_fields(
    block: Mapping[str, Any] | None, fields: Iterable[str] = DERIVED_FIELDS
) -> list[str]:
    """The derived fields that are blank in ``block`` (all of them when the block is absent)."""
    block = block or {}
    return [f for f in fields if is_blank(block.get(f))]


class StartingPointGap(str, Enum):
    NONE = "none"
    EXCLUDE = "exclude"
    ABSTAIN = "abstain"
    FAIL = "fail"


def classify_baseline_gap(
    blank: Iterable[str], *, built_this_run: bool, fields: Iterable[str] = DERIVED_FIELDS
) -> StartingPointGap:
    blank_set, all_fields = set(blank), set(fields)
    if not blank_set:
        return StartingPointGap.NONE
    if built_this_run:
        return StartingPointGap.FAIL
    if blank_set >= all_fields:
        return StartingPointGap.ABSTAIN
    return StartingPointGap.EXCLUDE


def fields_to_compare(
    blank: Iterable[str], gap: StartingPointGap, fields: Iterable[str] = DERIVED_FIELDS
) -> list[str]:
    """The derived fields a before/after comparison may judge; a blank baseline is not blamed."""
    if gap in (StartingPointGap.ABSTAIN, StartingPointGap.FAIL):
        return []
    skip = set(blank)
    return [f for f in fields if f not in skip]


# -- rejection attribution for the start-date detector ------------------------------------------

#: Rejections that say nothing about the start-date defect: a draft held elsewhere, a route
#: that does not exist, validators for other rules (a candidate already booked or not active).
START_CHANGE_ENVIRONMENT: tuple[str, ...] = (
    r"A draft already exists for this subscription",
    r"No API route for",
    r"unknown_path",
    r"Start date cannot change after the first booking",
    r"Subscription is not active",
)

#: A rejection that names the start date itself is the defect's own signal.
START_CHANGE_DEFECT: tuple[str, ...] = (r"\bstart[_ ]date\b", r"\bterm year\b")


def rejection_text(status: int, body: Mapping[str, Any] | None) -> str:
    """One line from a rejected response: status, error code, message and field errors."""
    body = body or {}
    fields = body.get("fields") or {}
    parts = [str(status), str(body.get("error") or ""), str(body.get("message") or "")]
    parts += [f"{k}: {v}" for k, v in fields.items()]
    return " ".join(p for p in parts if p).strip()
