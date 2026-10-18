"""QuickPizza visitors: they mostly read the menu and sometimes ask for a pizza.

The traffic matches the k6 example so the two tools can be compared: 4 menu reads for
every recommendation, one request per visit, and each customer paced to one visit per
second (50 users = 50 requests/s, the k6 load rate). Each request names itself
(``name=``), so the report groups by endpoint.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Protocol

from locust import HttpUser, TaskSet, constant_throughput, task

#: A stable User-Agent lets the owner of the target find (and filter) this traffic.
USER_AGENT = "locust-perf-suite"
#: Read-heavy, like most shops: 4 menu reads for every recommendation.
READ_WEIGHT = 4
ORDER_WEIGHT = 1

_user_numbers = itertools.count(1)


class _Response(Protocol):
    status_code: int

    def failure(self, exc: object) -> None: ...

    def json(self) -> Any: ...


@dataclass(frozen=True)
class Restriction:
    """One kind of customer: what they will and will not eat."""

    name: str
    max_calories_per_slice: int
    must_be_vegetarian: bool
    min_toppings: int
    max_toppings: int

    def body(self) -> dict[str, object]:
        return {
            "maxCaloriesPerSlice": self.max_calories_per_slice,
            "mustBeVegetarian": self.must_be_vegetarian,
            "excludedIngredients": [],
            "excludedTools": [],
            "minNumberOfToppings": self.min_toppings,
            "maxNumberOfToppings": self.max_toppings,
        }


RESTRICTIONS = (
    Restriction("anything", 1000, False, 2, 5),
    Restriction("vegetarian", 800, True, 2, 4),
    Restriction("light", 500, False, 1, 3),
)
MENU_PATHS = (("home", "/"), ("quotes", "/api/quotes"), ("names", "/api/names"))


def token_for(user_number: int) -> str:
    """QuickPizza's demo endpoints accept any 16-character token (quickpizza-local 0.15.31)."""
    return f"perf{user_number:012d}"


def expect_status(response: _Response, expected: int) -> bool:
    """Mark the request failed unless it answered ``expected``; True when it did."""
    if response.status_code != expected:
        response.failure(f"expected status {expected}, got {response.status_code}")
        return False
    return True


def check_recommendation(response: _Response, restriction: Restriction) -> None:
    """A fast wrong answer is still wrong: a pizza that breaks the restriction fails."""
    if not expect_status(response, 200):
        return
    answer = response.json()
    if answer.get("calories", 0) > restriction.max_calories_per_slice:
        limit = restriction.max_calories_per_slice
        response.failure(f"{answer.get('calories')} calories per slice, limit {limit}")
    elif restriction.must_be_vegetarian and answer.get("vegetarian") is not True:
        response.failure("asked for a vegetarian pizza, got one with meat")


class Visit(TaskSet):
    """A task set that hands control back to the user after each task.

    Without ``leave()`` a user that enters a task set never comes out of it, and the
    weights in ``SiteUser.tasks`` would only decide the first choice. Leaving without
    a reschedule means the user's ``wait_time`` applies before the next visit.
    """

    def leave(self) -> None:
        self.interrupt(reschedule=False)  # type: ignore[no-untyped-call]  # unannotated


class BrowseMenu(Visit):
    """Read-only visit: the home page, the quotes or the pizza names, in turn."""

    paths = itertools.cycle(MENU_PATHS)

    @task
    def menu(self) -> None:
        name, path = next(self.paths)
        with self.client.get(path, name=name, catch_response=True) as response:
            expect_status(response, 200)
        self.leave()


class OrderPizza(Visit):
    """Ask for a recommendation under the next restriction in turn."""

    restrictions = itertools.cycle(RESTRICTIONS)

    @task
    def recommend(self) -> None:
        restriction = next(self.restrictions)
        with self.client.post(
            "/api/pizza",
            json=restriction.body(),
            name="recommend",
            catch_response=True,
        ) as response:
            check_recommendation(response, restriction)
        self.leave()


class SiteUser(HttpUser):
    """One customer, starting one visit per second however long the last one took."""

    # Pacing, not a fixed pause: the request rate stays put when the target slows down,
    # the closest Locust gets to k6's arrival-rate executors.
    wait_time = constant_throughput(1)
    # {task set: weight}. Locust accepts this form; its annotation only lists the list form.
    tasks = {BrowseMenu: READ_WEIGHT, OrderPizza: ORDER_WEIGHT}  # type: ignore[assignment]

    def on_start(self) -> None:
        self.client.headers["User-Agent"] = USER_AGENT
        self.client.headers["Authorization"] = f"token {token_for(next(_user_numbers))}"
