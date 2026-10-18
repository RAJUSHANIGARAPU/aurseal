"""Simulated users: what they request, in what mix, and how long they pause.

A task set groups the requests of one kind of visit; the weights in ``SiteUser.tasks``
pick the next visit, one at a time. Each request names itself (``name=``), so the report
groups by endpoint instead of by every distinct URL.
"""

from __future__ import annotations

from typing import Protocol

from locust import HttpUser, TaskSet, between, task

#: A stable User-Agent lets the owner of the target find (and filter) this traffic.
USER_AGENT = "locust-perf-suite"


class _Response(Protocol):
    status_code: int

    def failure(self, exc: object) -> None: ...


def expect_status(response: _Response, expected: int) -> None:
    """Mark the request failed unless it answered ``expected``.

    A failure here counts towards the error rate the SLO check reads; it does not stop
    the user, so one bad response cannot hide the rest of the run.
    """
    if response.status_code != expected:
        response.failure(f"expected status {expected}, got {response.status_code}")


class Visit(TaskSet):
    """A task set that hands control back to the user after each task.

    Without ``leave()`` a user that enters a task set never comes out of it, and the
    weights in ``SiteUser.tasks`` would only decide the first choice. Leaving without a
    reschedule means the user's ``wait_time`` applies before the next visit.
    """

    def leave(self) -> None:
        self.interrupt(reschedule=False)  # type: ignore[no-untyped-call]  # unannotated


class Browse(Visit):
    """Read-only visits: on most sites, the bulk of the traffic."""

    @task
    def home(self) -> None:
        with self.client.get("/", name="home", catch_response=True) as response:
            expect_status(response, 200)
        self.leave()


class SiteUser(HttpUser):
    """One visitor, pausing 1-3 s between requests as a person reading a page would."""

    wait_time = between(1, 3)
    # {task set: weight}. Locust accepts this form; its annotation only lists the list form.
    tasks = {Browse: 1}  # type: ignore[assignment]

    def on_start(self) -> None:
        self.client.headers["User-Agent"] = USER_AGENT
