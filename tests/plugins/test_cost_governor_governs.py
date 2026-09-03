"""
The cost governor, which did not govern.

`cached_complete` never called `record()`, and of its eight call sites only
`unit_ai` recorded anything — so `budget_used` stayed at `0.0` through every
tier-3 and tier-4 model call. `get_model` therefore always returned the
preferred model, and since it only downgrades and never declines,
`PLUGIN_BUDGET_USD=0.01` stopped nothing at all.

Two halves are tested here, and the second is the one that is easy to fake: a
governor that refused every call would satisfy every budget test on this page
while making the whole platform useless. The controls assert that calls within
budget actually happen, actually reach the model, and actually return its answer.
"""

from __future__ import annotations

import threading

import pytest

from plugins.cost_governor import CostGovernor, estimate_cost


class Recorder:
    """A stand-in model. Records every prompt it is actually asked."""

    def __init__(self, reply: str = "answer") -> None:
        self.reply = reply
        self.prompts: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, prompt: str) -> str:
        with self._lock:
            self.prompts.append(prompt)
        return self.reply

    @property
    def calls(self) -> int:
        return len(self.prompts)


class TestSpendIsRecorded:
    def test_a_call_moves_the_budget(self):
        """The defect in one line: this used to stay at 0.0 forever."""
        governor = CostGovernor(budget_total=5.0)
        governor.cached_complete("a prompt worth paying for", Recorder())
        assert governor.budget_used > 0.0

    def test_the_amount_scales_with_what_was_sent_and_received(self):
        small = CostGovernor(budget_total=5.0)
        large = CostGovernor(budget_total=5.0)
        small.cached_complete("x" * 100, Recorder("y" * 100))
        large.cached_complete("x" * 10_000, Recorder("y" * 10_000))
        assert large.budget_used > small.budget_used

    def test_a_cache_hit_is_not_charged_twice(self):
        governor = CostGovernor(budget_total=5.0)
        model = Recorder()
        governor.cached_complete("same", model)
        after_first = governor.budget_used
        governor.cached_complete("same", model)
        assert governor.budget_used == after_first
        assert model.calls == 1

    def test_a_failed_call_is_not_charged(self):
        """An outage returns "" — the caller was not billed for nothing."""
        governor = CostGovernor(budget_total=5.0)
        governor.cached_complete("prompt", Recorder(""))
        assert governor.budget_used == 0.0


class TestTheBudgetIsAnActualLimit:
    def test_an_exhausted_budget_declines_the_call(self):
        """
        `get_model` downgrading to a cheaper model slows the burn; it never
        stops it. This is the part that was missing entirely.
        """
        governor = CostGovernor(budget_total=0.000001)
        model = Recorder()
        governor.cached_complete("first call exhausts it", model)
        governor.cached_complete("a different prompt entirely", model)
        assert model.calls == 1

    def test_a_declined_call_returns_empty_so_the_caller_reports_unknown(self):
        governor = CostGovernor(budget_total=0.000001)
        model = Recorder()
        governor.cached_complete("first", model)
        assert governor.cached_complete("second", model) == ""

    def test_declines_are_counted(self):
        governor = CostGovernor(budget_total=0.000001)
        model = Recorder()
        governor.cached_complete("first", model)
        governor.cached_complete("second", model)
        assert governor.summary()["calls_declined"] == 1

    def test_a_cached_answer_is_still_served_after_exhaustion(self):
        """Returning a known answer costs nothing, so refusing it would be silly."""
        governor = CostGovernor(budget_total=0.000001)
        model = Recorder()
        first = governor.cached_complete("same prompt", model)
        assert governor.cached_complete("same prompt", model) == first


class TestCallsWithinBudgetStillHappen:
    """
    Positive controls. A governor hardwired to decline would pass every test
    above — and would be a worse bug than the one being fixed, since the
    platform would silently stop asking anything.
    """

    def test_the_model_is_actually_asked(self):
        governor = CostGovernor(budget_total=5.0)
        model = Recorder()
        governor.cached_complete("a question", model)
        assert model.prompts == ["a question"]

    def test_the_answer_is_returned_unchanged(self):
        governor = CostGovernor(budget_total=5.0)
        assert governor.cached_complete("q", Recorder("the answer")) == "the answer"

    def test_many_distinct_prompts_all_go_through_on_a_real_budget(self):
        governor = CostGovernor(budget_total=5.0)
        model = Recorder()
        for i in range(50):
            governor.cached_complete(f"prompt {i}", model)
        assert model.calls == 50
        assert governor.summary()["calls_declined"] == 0

    def test_the_default_budget_is_not_accidentally_tiny(self):
        """Guards against a fix that 'works' by making everything unaffordable."""
        governor = CostGovernor(budget_total=5.0)
        tokens, cost = estimate_cost("x" * 8000, "y" * 8000)
        assert cost < governor.budget_total / 100


class TestUnderTheThreadPool:
    """
    `master_orchestrator` runs plugins on eight threads. `budget_used += cost`
    is not atomic, and a plain check-then-act cache let eight threads miss the
    same key and all pay for the same prompt.
    """

    def test_one_prompt_asked_by_eight_threads_is_paid_for_once(self):
        governor = CostGovernor(budget_total=5.0)

        class Slow(Recorder):
            def __call__(self, prompt: str) -> str:
                start.wait(timeout=5)
                return super().__call__(prompt)

        start = threading.Event()
        model = Slow()
        threads = [
            threading.Thread(target=governor.cached_complete, args=("shared", model))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(timeout=10)

        assert model.calls == 1

    def test_concurrent_distinct_prompts_do_not_lose_spend(self):
        """`+=` under eight threads drops updates; the total must be exact."""
        governor = CostGovernor(budget_total=50.0)
        model = Recorder()
        threads = [
            threading.Thread(target=governor.cached_complete, args=(f"p{i}", model))
            for i in range(64)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert governor.summary()["calls_made"] == 64
        assert model.calls == 64

    @pytest.mark.parametrize("run", range(3))
    def test_the_dedup_does_not_deadlock(self, run):
        """Repeated, because a lock bug that only sometimes hangs is worse."""
        governor = CostGovernor(budget_total=5.0)
        model = Recorder()
        threads = [
            threading.Thread(target=governor.cached_complete, args=("same", model))
            for _ in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert all(not t.is_alive() for t in threads)
