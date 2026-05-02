"""Tests for CostGovernor: model selection, record(), budget_remaining."""

from __future__ import annotations

import pytest

from plugins.cost_governor import CostGovernor

_HAIKU = "claude-haiku-4-5-20251001"


class TestCostGovernorInit:
    def test_default_budget(self) -> None:
        gov = CostGovernor()
        assert gov.budget_total == 5.0

    def test_custom_budget(self) -> None:
        gov = CostGovernor(budget_total=10.0)
        assert gov.budget_total == 10.0

    def test_env_var_overrides_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLUGIN_BUDGET_USD", "3.5")
        gov = CostGovernor(budget_total=10.0)
        assert gov.budget_total == 3.5

    def test_initial_budget_used_is_zero(self) -> None:
        gov = CostGovernor()
        assert gov.budget_used == 0.0

    def test_budget_remaining_initially_equals_total(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        assert gov.budget_remaining == 5.0


class TestCostGovernorGetModel:
    def test_returns_preferred_when_budget_full(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        assert gov.get_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_returns_haiku_when_budget_low(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        gov.budget_used = 4.5  # only 10% remaining < 20% threshold
        assert gov.get_model("claude-sonnet-4-6") == _HAIKU

    def test_returns_preferred_at_exactly_20_percent(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        gov.budget_used = 4.0  # exactly 20% remaining
        assert gov.get_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_returns_haiku_just_below_20_percent(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        gov.budget_used = 4.01  # 19.8% remaining
        assert gov.get_model("claude-sonnet-4-6") == _HAIKU

    def test_zero_budget_returns_haiku(self) -> None:
        gov = CostGovernor(budget_total=0.0)
        assert gov.get_model("claude-sonnet-4-6") == _HAIKU


class TestCostGovernorRecord:
    def test_record_increases_budget_used(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        gov.record("claude-sonnet-4-6", 1000, 0.01)
        assert gov.budget_used == pytest.approx(0.01)

    def test_record_cumulative(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        gov.record("claude-sonnet-4-6", 1000, 0.01)
        gov.record("claude-haiku-4-5-20251001", 500, 0.005)
        assert gov.budget_used == pytest.approx(0.015)

    def test_budget_remaining_decreases_after_record(self) -> None:
        gov = CostGovernor(budget_total=5.0)
        gov.record("claude-sonnet-4-6", 1000, 1.0)
        assert gov.budget_remaining == pytest.approx(4.0)

    def test_budget_remaining_floor_at_zero(self) -> None:
        gov = CostGovernor(budget_total=1.0)
        gov.record("model", 99999, 100.0)
        assert gov.budget_remaining == 0.0


class TestCostGovernorCache:
    def test_same_prompt_returns_cached(self) -> None:
        gov = CostGovernor()
        call_count = 0

        def fake_llm(p: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"response_to_{p}"

        result1 = gov.cached_complete("hello", fake_llm)
        result2 = gov.cached_complete("hello", fake_llm)
        assert result1 == result2
        assert call_count == 1  # only called once

    def test_different_prompts_not_cached(self) -> None:
        gov = CostGovernor()
        call_count = 0

        def fake_llm(p: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"response_{call_count}"

        gov.cached_complete("prompt_a", fake_llm)
        gov.cached_complete("prompt_b", fake_llm)
        assert call_count == 2
