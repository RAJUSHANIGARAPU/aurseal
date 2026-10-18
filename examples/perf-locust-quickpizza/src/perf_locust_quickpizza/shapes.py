"""Load shapes: how the number of users changes during a run.

``StepRamp`` drives the stress profile. It adds users step by step past the expected
peak and stops early once the error budget in ``slo.json`` is spent: past that point the
run measures a broken system, and the user count it reached is the finding.
"""

from __future__ import annotations

import math
import time
from functools import cached_property

from locust import LoadTestShape

from .slo import load_budgets


class StepRamp(LoadTestShape):
    step_users = 10
    step_s = 20
    max_users = 100
    hold_s = 30
    spawn_rate = 10.0
    #: Seconds before the error budget is enforced, so warm-up errors settle first.
    grace_s = 10

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]  # Locust leaves it unannotated

    @cached_property
    def abort_error_rate(self) -> float:
        """The stress profile's error budget from slo.json."""
        return load_budgets()["stress"].error_rate

    def plan(self, elapsed: float) -> tuple[int, float] | None:
        """Users and spawn rate ``elapsed`` seconds into the run; ``None`` ends it."""
        ramp_s = math.ceil(self.max_users / self.step_users) * self.step_s
        if elapsed >= ramp_s + self.hold_s:
            return None
        users = min(self.max_users, (int(elapsed // self.step_s) + 1) * self.step_users)
        return users, self.spawn_rate

    def budget_spent(self, elapsed: float, fail_ratio: float) -> bool:
        return elapsed >= self.grace_s and fail_ratio > self.abort_error_rate

    def tick(self) -> tuple[int, float] | None:
        elapsed = time.perf_counter() - self.start_time
        fail_ratio = self.runner.stats.total.fail_ratio if self.runner else 0.0
        if self.budget_spent(elapsed, fail_ratio):
            return None
        return self.plan(elapsed)
