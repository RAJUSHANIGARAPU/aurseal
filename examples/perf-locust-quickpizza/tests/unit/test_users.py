"""The simulated customers: the traffic mix, the pacing, and what counts as a failure."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pytest

from perf_locust_quickpizza.users import (
    ORDER_WEIGHT,
    READ_WEIGHT,
    RESTRICTIONS,
    BrowseMenu,
    OrderPizza,
    Restriction,
    SiteUser,
    Visit,
    check_recommendation,
    expect_status,
    token_for,
)

pytestmark = pytest.mark.offline


@dataclass
class FakeResponse:
    status_code: int
    body: dict[str, Any] = field(default_factory=dict)
    failures: list[object] = field(default_factory=list)

    def failure(self, exc: object) -> None:
        self.failures.append(exc)

    def json(self) -> Any:
        return self.body


def test_user_tasks_weighted_to_read_heavy_mix():
    # Locust expands {task set: weight} into a list with one entry per unit of weight.
    mix = Counter(SiteUser.tasks)
    assert mix == {BrowseMenu: READ_WEIGHT, OrderPizza: ORDER_WEIGHT}
    assert mix[BrowseMenu] > mix[OrderPizza]


def test_every_task_set_hands_control_back_to_the_user():
    assert all(issubclass(task_set, Visit) for task_set in set(SiteUser.tasks))


def test_tokens_are_the_sixteen_characters_quickpizza_accepts():
    tokens = {token_for(n) for n in (1, 2, 999)}
    assert len(tokens) == 3
    assert all(len(token) == 16 for token in tokens)


@pytest.mark.parametrize("restriction", RESTRICTIONS, ids=lambda r: r.name)
def test_a_recommendation_inside_the_restriction_passes(restriction):
    body = {"calories": restriction.max_calories_per_slice, "vegetarian": True}
    response = FakeResponse(200, body)
    check_recommendation(response, restriction)
    assert response.failures == []


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        pytest.param(503, {}, "status", id="server-error"),
        pytest.param(200, {"calories": 501, "vegetarian": True}, "calories", id="too-heavy"),
        pytest.param(200, {"calories": 100, "vegetarian": False}, "vegetarian", id="meat"),
    ],
)
def test_a_wrong_recommendation_is_a_failure(status, body, reason):
    light_vegetarian = Restriction("light-vegetarian", 500, True, 1, 3)
    response = FakeResponse(status, body)
    check_recommendation(response, light_vegetarian)
    assert len(response.failures) == 1
    assert reason in str(response.failures[0])


def test_the_expected_status_is_not_a_failure():
    response = FakeResponse(200)
    assert expect_status(response, 200)
    assert response.failures == []
