"""Tests for oracles.ledger_journal: mapping, references, and the measured-absence guard."""

from __future__ import annotations

from decimal import Decimal

import pytest

from oracles import ledger_journal as lj

MAPPINGS = {"RECEIVABLE": "1300", "FEES": "8100"}


def _entry(refs, *lines, line_refs=None):
    return {
        "references": list(refs),
        "lines": [
            {
                "account": a,
                "debit": d,
                "credit": c,
                **({"references": line_refs} if line_refs else {}),
            }
            for a, d, c in lines
        ],
    }


REGULAR = _entry(["TX-1", "100001"], ("1300", 18.0, 0.0), ("8100", 0.0, 15.0), ("1500", 0.0, 3.0))
CANCEL_CLEAN = _entry(
    ["TX-9", "100009"], ("1300", 0.0, 12.0), ("8000", 10.0, 0.0), ("1500", 2.0, 0.0)
)
CANCEL_FEE = _entry(["TX-9", "100009"], ("1300", 6.0, 0.0), ("8100", 0.0, 5.0), ("1500", 0.0, 1.0))


def test_account_for_mapping_reads_dict_and_list_shapes():
    assert lj.account_for_mapping(MAPPINGS, "FEES") == "8100"
    rows = [{"role": "FEES", "account": "8100"}, {"role": "TAX", "account": "1500"}]
    assert lj.account_for_mapping(rows, "TAX") == "1500"


def test_unknown_role_raises_instead_of_guessing():
    with pytest.raises(LookupError, match="no ledger account is mapped to role 'COSTS'"):
        lj.account_for_mapping(MAPPINGS, "COSTS")


def test_references_of_collects_scalars_and_lists():
    tx = {"reference": "TX-9", "references": ["100009", None, ""]}
    assert lj.references_of(tx, ("reference", "references")) == {"TX-9", "100009"}
    assert lj.references_of({"reference": None}) == set()


def test_entries_referencing_matches_entry_or_line_references():
    by_line = _entry([], ("8100", 0.0, 4.0), line_refs=["TX-7"])
    entries = [REGULAR, CANCEL_CLEAN, by_line]
    assert lj.entries_referencing(entries, {"TX-9"}) == [CANCEL_CLEAN]
    assert lj.entries_referencing(entries, {"TX-7"}) == [by_line]


def test_lines_for_references_skips_zero_lines_and_other_accounts():
    zero = _entry(["TX-9"], ("8100", 0.0, 0.0))
    found = lj.lines_for_references([REGULAR, CANCEL_FEE, zero], {"TX-9"}, "8100")
    assert found == [CANCEL_FEE["lines"][1]]
    assert lj.booked_amount(found) == Decimal("-5.0")


def test_claim_absent_passes_when_the_account_is_measured_elsewhere():
    lj.claim_absent_on_account([REGULAR, CANCEL_CLEAN], {"TX-9"}, "8100")


def test_claim_absent_fails_naming_the_lines_when_present():
    with pytest.raises(AssertionError, match="account 8100 carries 1 line"):
        lj.claim_absent_on_account([REGULAR, CANCEL_FEE], {"TX-9"}, "8100")


def test_claim_absent_refuses_when_the_account_carries_nothing_elsewhere():
    with pytest.raises(lj.NotMeasured, match="absence of measurement"):
        lj.claim_absent_on_account([CANCEL_CLEAN], {"TX-9"}, "8100")
    with pytest.raises(lj.NotMeasured):
        lj.claim_absent_on_account([], {"TX-9"}, "8100")


def test_claim_absent_refuses_without_references():
    with pytest.raises(lj.NotMeasured, match="no reference ids"):
        lj.claim_absent_on_account([REGULAR], set(), "8100")


def test_shape_is_configurable():
    shape = lj.JournalShape(
        entry_references=("sourceReference",), lines="items", account="gl", debit="dr", credit="cr"
    )
    entry = {"sourceReference": "R-1", "items": [{"gl": "9", "dr": 0, "cr": 2}]}
    assert lj.lines_for_references([entry], ["R-1"], "9", shape) == entry["items"]
    assert "['R-1'] -> 9:-2" in lj.describe([entry], shape)
    assert lj.describe([]) == "no journal entries"
