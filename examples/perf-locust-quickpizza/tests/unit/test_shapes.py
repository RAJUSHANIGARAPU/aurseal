"""The stress profile's load shape: it ramps, holds, and stops when the budget is spent."""

from __future__ import annotations

import pytest

from perf_locust_quickpizza.shapes import StepRamp

pytestmark = pytest.mark.offline


@pytest.fixture
def ramp() -> StepRamp:
    return StepRamp()


def test_users_step_up_until_the_maximum(ramp):
    counts = [plan[0] for t in range(0, 300, 5) if (plan := ramp.plan(float(t)))]
    assert counts[0] == ramp.step_users
    assert counts == sorted(counts)
    assert max(counts) == ramp.max_users


def test_the_run_ends_after_the_hold(ramp):
    ramp_s = (ramp.max_users // ramp.step_users) * ramp.step_s
    assert ramp.plan(ramp_s + ramp.hold_s - 1) is not None
    assert ramp.plan(ramp_s + ramp.hold_s) is None


@pytest.mark.parametrize(
    ("elapsed", "fail_ratio", "spent"),
    [
        pytest.param(5.0, 0.5, False, id="warm-up-errors-are-ignored"),
        pytest.param(60.0, 0.001, False, id="inside-the-budget"),
        pytest.param(60.0, 0.05, True, id="budget-spent-stops-the-run"),
    ],
)
def test_the_error_budget_stops_the_ramp(ramp, elapsed, fail_ratio, spent):
    assert ramp.budget_spent(elapsed, fail_ratio) is spent
