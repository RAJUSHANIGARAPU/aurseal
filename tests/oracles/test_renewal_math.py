"""Renewal oracles: indexation figures, the bare-multiplier misread, year clamp, schedule checks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from oracles import ledger_checks as lc
from oracles.charges import (
    READINGS,
    ChargeSchedule,
    bare_multiplier_misread,
    explain_indexed,
    indexed_amount,
    indexed_renewal,
    truncated_percent_misread,
)
from verification._temporal import add_years_clamped

SCHEDULE = ChargeSchedule({1: D("0.03"), 12: D("0")}, D("0.20"))


class TestIndexation:
    def test_indexed_is_base_times_one_plus_factor(self):
        assert indexed_amount(D("40.00"), D("0.025")) == D("41.00")
        assert indexed_amount(D("12.50"), D("0.033")) == D("12.91")  # 12.9125 half-up

    def test_misread_is_base_times_factor(self):
        assert bare_multiplier_misread(D("40.00"), D("0.10")) == D("4.00")

    def test_truncated_percent_misread_drops_the_decimals(self):
        assert truncated_percent_misread(D("32.50"), D("0.025")) == D("33.15")
        assert truncated_percent_misread(D("32.50"), D("0.03")) == indexed_amount(
            D("32.50"), D("0.03")
        )
        assert READINGS["percent_truncated_misread"] == "index percent truncated to a whole number"

    def test_renewal_chain_surcharge_net_tax_total(self):
        yearly = indexed_renewal(SCHEDULE, D("40.00"), D("0.025"), 12)
        assert (yearly.gross, yearly.surcharge, yearly.net, yearly.tax, yearly.total) == (
            D("492.00"),
            D("0.00"),
            D("492.00"),
            D("98.40"),
            D("590.40"),
        )
        monthly = indexed_renewal(SCHEDULE, D("40.00"), D("0.025"), 1)
        assert monthly.surcharge == D("1.23") and monthly.net == D("42.23")
        assert monthly.additive and yearly.additive

    @pytest.mark.parametrize(
        ("observed", "reading"),
        [
            (D("41.00"), "indexed"),
            (D("1.00"), "bare_multiplier_misread"),
            (D("40.80"), "percent_truncated_misread"),
            (D("40.00"), "not_indexed"),
            (D("41.10"), "unexplained"),
        ],
    )
    def test_explain_names_the_reading(self, observed, reading):
        assert explain_indexed(observed, D("40.00"), D("0.025")) == reading


class TestYearClamp:
    @pytest.mark.parametrize(
        ("start", "years", "end"),
        [
            (date(2026, 3, 15), 1, date(2027, 3, 15)),
            (date(2024, 2, 29), 1, date(2025, 2, 28)),
            (date(2024, 2, 29), 4, date(2028, 2, 29)),
            (date(2025, 12, 31), 1, date(2026, 12, 31)),
        ],
    )
    def test_one_calendar_year_with_leap_day_clamp(self, start, years, end):
        assert add_years_clamped(start, years) == end


ROWS = [
    {"kind": "NEW", "start": date(2025, 3, 15), "net": "480.00"},
    {"kind": "RENEW", "start": date(2026, 3, 15), "net": "492.00"},
]


class TestRenewalSchedule:
    def test_healthy_renewal_row_starts_on_previous_term_end(self):
        assert lc.renewal_row_starts_on(ROWS, date(2026, 3, 15)) == []

    def test_defective_renewal_row_starting_a_day_late(self):
        late = [ROWS[0], {**ROWS[1], "start": date(2026, 3, 16)}]
        (problem,) = lc.renewal_row_starts_on(late, date(2026, 3, 15))
        assert "starts 2026-03-16" in problem and "2026-03-15" in problem

    def test_missing_or_duplicate_renewal_row(self):
        assert lc.renewal_row_starts_on(ROWS[:1], date(2026, 3, 15)) == [
            "expected one kind='RENEW' row, found 0"
        ]
        assert "found 2" in lc.renewal_row_starts_on([*ROWS, ROWS[1]], date(2026, 3, 15))[0]

    def test_healthy_renewal_collects_indexed_amount(self):
        assert lc.collects_indexed_amount(ROWS[1], D("492.00"), D("480.00")) == []

    def test_defective_renewal_collects_pre_index_amount(self):
        (problem,) = lc.collects_indexed_amount({"net": 480}, D("492.00"), D("480.00"))
        assert "pre-index net 480" in problem

    def test_unexplained_and_missing_amount(self):
        assert "neither" in lc.collects_indexed_amount({"net": 1}, D("492"), D("480"))[0]
        assert lc.collects_indexed_amount({}, D("492"), D("480")) == ["renewal row has no net"]
