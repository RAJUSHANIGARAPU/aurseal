"""Tests for daycount, period_window, charges, ledger_checks, recompute and reconciliation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from oracles import daycount as dc
from oracles import ledger_checks as lc
from oracles.charges import ChargeSchedule, sign_matches
from oracles.period_window import NoWindow, Window, choose_mid_period_date, last_booked_period
from oracles.recompute import RecomputeCheck
from oracles.reconciliation import reconcile

C = dc.DayCount


class TestThirty360:
    @pytest.mark.parametrize(
        "start,end,isda,us,e,e_isda",
        [
            # the 31st as END date is where ISDA and 30E/360 part ways
            (date(2030, 1, 15), date(2030, 3, 31), 76, 76, 75, 75),
            # once D1 is 30/31 they agree again
            (date(2030, 1, 31), date(2030, 3, 31), 60, 60, 60, 60),
            (date(2030, 1, 30), date(2030, 3, 31), 60, 60, 60, 60),
            # end of February: only the US and 30E ISDA rules treat it as the 30th
            (date(2030, 2, 28), date(2030, 3, 31), 33, 30, 32, 30),
            (date(2030, 2, 28), date(2030, 3, 15), 17, 15, 17, 15),
            (date(2028, 2, 29), date(2028, 8, 31), 182, 180, 181, 180),
            (date(2030, 3, 1), date(2030, 4, 1), 30, 30, 30, 30),
        ],
    )
    def test_conventions(self, start, end, isda, us, e, e_isda):
        assert dc.days_30_360(start, end, C.THIRTY_360_ISDA) == isda
        assert dc.days_30_360(start, end, C.THIRTY_360_US) == us
        assert dc.days_30_360(start, end, C.THIRTY_E_360) == e
        assert dc.days_30_360(start, end, C.THIRTY_E_360_ISDA) == e_isda

    def test_february_maturity_exception(self):
        start, end = date(2030, 1, 15), date(2030, 2, 28)
        assert dc.days_30_360(start, end, C.THIRTY_E_360_ISDA) == 45
        assert dc.days_30_360(start, end, C.THIRTY_E_360_ISDA, end_is_maturity=True) == 43

    def test_year_fractions(self):
        s, e = date(2030, 1, 1), date(2030, 7, 1)
        assert dc.year_fraction(s, e, C.ACT_365F) == D(181) / D(365)
        assert dc.year_fraction(s, e, C.ACT_360) == D(181) / D(360)
        assert dc.year_fraction(s, e, C.THIRTY_E_360) == D("0.5")
        with pytest.raises(ValueError):
            dc.year_fraction(s, e, C.ACT_ACT)
        with pytest.raises(ValueError):
            dc.days_30_360(s, e, C.ACT_360)


# Period 2030-03-15..2030-04-14 (inclusive), cancelled from 2030-03-31.
ON_THE_31ST = dc.Period(date(2030, 3, 15), date(2030, 4, 14), date(2030, 3, 31))
FULL = D("90.00")


class TestRefunds:
    def test_the_31st_separates_isda_from_30e(self):
        refunds = dc.candidate_refunds(ON_THE_31ST, FULL)
        assert refunds[C.THIRTY_360_ISDA] == D("-42.00")  # 31st counts: 14 of 30 unused
        assert refunds[C.THIRTY_E_360] == D("-45.00")  # 31st capped: 15 of 30 unused
        assert refunds[C.ACT_ACT] == D("-43.55")  # 15 of 31 unused
        assert dc.matched_conventions(ON_THE_31ST, FULL, D("-45.00")) == [
            C.THIRTY_E_360,
            C.THIRTY_E_360_ISDA,
        ]

    def test_period_starting_on_the_31st_is_still_a_whole_term(self):
        p = dc.Period(date(2030, 8, 31), date(2030, 9, 29), date(2030, 9, 15))
        assert dc.unused_fraction(p, C.THIRTY_E_360) == D("0.5")

    def test_describe_match_never_reads_a_tie_as_evidence(self):
        ordinary = dc.Period(date(2030, 3, 1), date(2030, 3, 30), date(2030, 3, 16))
        many = dc.describe_match(ordinary, D("60.00"), D("-30.00"))
        assert "cannot tell them apart" in many
        assert dc.describe_match(ON_THE_31ST, FULL, D("-43.55")).endswith("pins the convention")
        assert dc.describe_match(ON_THE_31ST, FULL, D("-1.00")) == "no modelled convention matches"
        assert dc.matched_conventions(ON_THE_31ST, FULL, None) == []

    @pytest.mark.parametrize("refund", ["-42.00", "-45.00", "-43.55"])
    def test_bounds_accept_any_modelled_convention(self, refund):
        dc.assert_pro_rata_refund(
            "case",
            ON_THE_31ST,
            FULL,
            D(refund),
            refund_tax=D(refund) * D("0.2"),
            tax_rate=D("0.20"),
        )

    @pytest.mark.parametrize(
        "refund,needle",
        [
            (D("0"), "negative"),
            (D("45.00"), "negative"),
            (D("-90.00"), "strictly smaller"),
            (None, "negative"),
            (D("-12.00"), "wrong period"),  # negative, partial, taxed — and still wrong
        ],
    )
    def test_bounds_reject_broken_refunds(self, refund, needle):
        with pytest.raises(AssertionError, match=needle):
            dc.assert_pro_rata_refund("case", ON_THE_31ST, FULL, refund)

    def test_tax_ratio_is_checked_when_a_rate_is_given(self):
        with pytest.raises(AssertionError, match="tax"):
            dc.assert_pro_rata_refund(
                "case", ON_THE_31ST, FULL, D("-45.00"), refund_tax=D("-1.00"), tax_rate=D("0.2")
            )

    def test_mid_period_flag_and_degenerate_period(self):
        assert ON_THE_31ST.is_mid_period
        assert not dc.Period(date(2030, 1, 1), date(2030, 1, 31), date(2030, 2, 1)).is_mid_period
        empty = dc.Period(date(2030, 1, 2), date(2030, 1, 1), date(2030, 1, 2))
        assert dc.unused_fraction(empty, C.ACT_ACT) == 0


class TestPeriodWindow:
    PERIOD = Window(date(2030, 1, 1), date(2030, 3, 31))

    def test_days_into_and_midpoint_fallback(self):
        assert choose_mid_period_date(self.PERIOD, days_into=45) == date(2030, 2, 15)
        assert choose_mid_period_date(self.PERIOD, days_into=400) == date(2030, 2, 14)

    def test_blocked_and_validity_end_are_explained(self):
        blocked = [Window(date(2030, 2, 10), date(2030, 2, 20))]
        out = choose_mid_period_date(self.PERIOD, days_into=45, blocked=blocked)
        assert isinstance(out, NoWindow) and "scheduled" in out.reason and not out
        out = choose_mid_period_date(self.PERIOD, days_into=45, valid_to=date(2030, 2, 1))
        assert "validity end" in out.reason

    def test_no_interior_day(self):
        tiny = Window(date(2030, 1, 1), date(2030, 1, 2))
        assert "no interior day" in choose_mid_period_date(tiny, days_into=1).reason

    def test_last_booked_period(self):
        periods = [
            (date(2030, 1, 1), date(2030, 1, 31), "BOOKED"),
            (date(2030, 2, 1), date(2030, 2, 28), "BOOKED"),
            (date(2030, 3, 1), date(2030, 3, 31), "SCHEDULED"),
        ]
        assert last_booked_period(periods) == Window(date(2030, 2, 1), date(2030, 2, 28))
        assert last_booked_period([]) is None


SCHEDULE = ChargeSchedule({1: D("0.05"), 3: D("0.03"), 12: D("0")}, tax_rate=D("0.20"))


class TestCharges:
    def test_compute_rounds_half_up_and_is_additive(self):
        c = SCHEDULE.compute(D("10.10"), 1)
        assert (c.surcharge, c.net, c.tax, c.total) == (
            D("0.51"),
            D("10.61"),
            D("2.12"),
            D("12.73"),
        )
        assert c.additive

    def test_installment_delta_and_ceiling(self):
        delta = SCHEDULE.installment_delta(D("10.00"), D("12.00"), 3)
        assert (delta.net, delta.total) == (D("2.06"), D("2.47"))
        assert SCHEDULE.period_delta_ceiling(D("10.00"), D("12.00"), 3) == D("8.24")
        assert SCHEDULE.installments_per_period(1) == 12

    def test_unknown_term_and_bad_division(self):
        with pytest.raises(ValueError, match="no surcharge rate"):
            SCHEDULE.compute(D("1"), 6)
        with pytest.raises(ValueError):
            SCHEDULE.installments_per_period(5)

    def test_sign_matches(self):
        assert sign_matches(D("2"), D("0.5")) and sign_matches(D("-2"), D("-1"))
        assert sign_matches(D("0"), D("0")) and not sign_matches(D("2"), D("-1"))


ROWS = [
    {
        "start": date(2030, 1, 1),
        "end": date(2030, 1, 31),
        "net": "10",
        "tax": "2",
        "total": "12",
        "code": "FIRST",
    },
    {
        "start": date(2030, 2, 1),
        "end": date(2030, 2, 28),
        "net": "10",
        "tax": "2",
        "total": "12",
        "code": "NEXT",
    },
    {
        "start": date(2030, 3, 1),
        "end": date(2030, 3, 31),
        "net": "10",
        "tax": "2",
        "total": "12",
        "code": "NEXT",
    },
]


class TestLedgerChecks:
    def test_a_well_formed_schedule_passes_everything(self):
        assert lc.additive_identity(ROWS, ["net", "tax"], "total") == []
        assert lc.contiguous(ROWS) == [] and lc.steps_match_term(ROWS, 1) == []
        assert lc.allowed_values(ROWS, "code", {"FIRST", "NEXT"}) == []
        assert lc.sequence(ROWS, "code", "FIRST", "NEXT") == []

    def test_each_check_bites(self):
        bad = [dict(r) for r in ROWS]
        bad[1]["total"] = "13"
        bad[2]["start"] = date(2030, 3, 2)
        bad[0]["code"] = "OTHER"
        del bad[0]["tax"]
        assert len(lc.additive_identity(bad, ["net", "tax"], "total")) == 2
        assert "gap" in lc.contiguous(bad)[0]
        assert lc.steps_match_term(bad, 1)
        assert lc.allowed_values(bad, "code", {"FIRST", "NEXT"})
        assert lc.sequence(bad, "code", "FIRST", "NEXT")
        overlap = [ROWS[0], {**ROWS[1], "start": date(2030, 1, 31)}]
        assert "overlap" in lc.contiguous(overlap)[0]
        assert (
            lc.steps_match_term([{"start": date(2030, 1, 31), "end": date(2030, 4, 29)}], 3) == []
        )


class TestRecompute:
    def test_only_non_zero_baseline_paths_count(self):
        check = RecomputeCheck.of(
            {"a.net": D("10"), "a.tax": D("0"), "a.fee": None, "a.total": D("12")},
            {"a.net": D("0"), "a.total": D("12"), "a.tax": D("3")},
        )
        assert check.stale_paths == ["a.total"] and not check.recomputed
        assert "stale=['a.total']" in check.describe()

    def test_absent_after_counts_as_recomputed(self):
        assert RecomputeCheck.of({"x": D("5")}, {}).recomputed

    def test_empty_baseline_is_no_verdict(self):
        check = RecomputeCheck.of({"x": D("0")}, {"x": D("9")})
        assert check.baseline_is_empty and not check.recomputed and check.stale_paths == []


DOC = """Statement period 01-03-2030 up to 31-03-2030
Net amount        -€ 50,00
Tax               -€ 10,00
Total             -€ 60,00
"""


class TestReconciliation:
    MAP = {"Net amount": "net", "Tax": "tax", "Total": "total"}

    def test_agreeing_document_and_ledger(self):
        ledger = {"net": D("-50.00"), "tax": D("-10.00"), "total": D("-60.00")}
        result = reconcile(DOC, ledger, self.MAP, expected_sign=-1)
        assert result.ok and "agree" in result.describe()

    def test_mismatch_kinds(self):
        ledger = {"net": D("-50.01"), "tax": D("-10.50"), "fee": D("1")}
        result = reconcile(DOC, ledger, {**self.MAP, "Fee": "fee"})
        kinds = {(m.label, m.kind) for m in result.mismatches}
        assert kinds == {
            ("Tax", "amount"),
            ("Total", "missing-in-ledger"),
            ("Fee", "missing-on-document"),
        }

    def test_magnitudes_with_an_explicit_sign_check(self):
        positive_doc = DOC.replace("-€", "€")
        ledger = {"net": D("-50.00"), "tax": D("-10.00"), "total": D("-60.00")}
        assert reconcile(positive_doc, ledger, self.MAP, compare_magnitudes=True).ok
        wrong_sign = {k: -v for k, v in ledger.items()}
        result = reconcile(
            positive_doc, wrong_sign, self.MAP, compare_magnitudes=True, expected_sign=-1
        )
        assert {m.kind for m in result.mismatches} == {"sign"}
