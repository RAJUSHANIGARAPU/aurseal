"""Client for an outbound-messages API: read messages, wait for their files, generate them.

A test suite that inspects what a system is about to send to its partners must never be the
thing that sends it. This client therefore has reads, and one write that delivers nothing:

* :meth:`overview` — messages (optionally for one subject or since an instant), newest first;
* :meth:`details` / :meth:`wait_for_files` — one message, polling until its files exist;
* :meth:`download` — a generated file's bytes (:class:`NotGenerated` while there are none);
* :meth:`generate` — turn ``NEW`` messages into stored files (``TO_BE_SENT``), polling the
  background job to its end. Generating stores files and sends nothing.

There is deliberately no ``send`` or ``resend``: delivering to a partner is out of reach for
code holding this client, not just out of habit.

Paths default to the demo service's layout (``messages``, ``messages/generate``,
``jobs/{id}``) relative to the engine's base URL; pass others for another service.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from api.clients.http_engine import ApiError, HttpEngine

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_POLL_S = 0.1


class NotGenerated(LookupError):
    """The message has no generated file (yet)."""


class StillWaiting(TimeoutError):
    """A poll ran out of time; the message carries the last state seen."""


@dataclass(frozen=True)
class Paths:
    messages: str = "messages"
    generate: str = "messages/generate"
    job: str = "jobs/{id}"
    content: str = "messages/{id}/content"


class OutboundMessagesClient:
    def __init__(
        self,
        engine: HttpEngine,
        *,
        paths: Paths | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        poll_s: float = DEFAULT_POLL_S,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = engine
        self._paths = paths or Paths()
        self._timeout_s = timeout_s
        self._poll_s = poll_s
        self._sleep = sleep
        self._clock = clock

    # -- reads ----------------------------------------------------------------------------------

    def overview(
        self,
        subject_id: Any = None,
        *,
        since: str | None = None,
        subject_param: str = "subscription_id",
    ) -> list[dict[str, Any]]:
        """Messages, newest first (sorted here too, so callers never depend on server order)."""
        params: dict[str, Any] = {}
        if subject_id is not None:
            params[subject_param] = subject_id
        if since:
            params["since"] = since
        rows = self._api.get(self._paths.messages, params=params or None).raise_for_status().json()
        return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)

    def details(self, message_id: str) -> dict[str, Any]:
        return self._api.get(f"{self._paths.messages}/{message_id}").raise_for_status().json()

    def wait_for_files(self, message_id: str) -> dict[str, Any]:
        """Poll the details until the message lists files; :class:`StillWaiting` on timeout."""
        return self._poll(
            lambda: self.details(message_id),
            lambda d: bool(d.get("files")),
            f"message {message_id} has no files",
        )

    def download(self, message_id: str, *, fmt: str | None = None) -> bytes:
        """A generated file's bytes; :class:`NotGenerated` while the message has none."""
        response = self._api.get(
            self._paths.content.format(id=message_id),
            params={"format": fmt} if fmt else None,
            headers={"Accept": "*/*"},
        )
        if response.status == 404:
            raise NotGenerated(f"message {message_id} has no generated content: {response}")
        if not response.is_success:
            raise ApiError(response)
        return response.content

    # -- the one write ----------------------------------------------------------------------------

    def generate(self, subject_id: Any = None, *, subject_param: str = "subscription_id") -> dict:
        """Generate files for NEW messages and wait for the job; returns the finished job.

        This stores files and moves messages to TO_BE_SENT. It never delivers anything.
        """
        body: Mapping[str, Any] = {subject_param: subject_id} if subject_id is not None else {}
        started = self._api.post(self._paths.generate, json=body).raise_for_status().json()
        job_id = started.get("job_id", started.get("id"))
        return self._poll(
            lambda: self._api.get(self._paths.job.format(id=job_id)).raise_for_status().json(),
            lambda job: job.get("state") in ("DONE", "FAILED"),
            f"generate job {job_id} did not finish",
        )

    # -- internals --------------------------------------------------------------------------------

    def _poll(
        self, read: Callable[[], dict], done: Callable[[dict], bool], what: str
    ) -> dict[str, Any]:
        deadline = self._clock() + self._timeout_s
        while True:
            last = read()
            if done(last):
                return last
            if self._clock() >= deadline:
                raise StillWaiting(f"{what} after {self._timeout_s}s; last seen: {last}")
            self._sleep(self._poll_s)
