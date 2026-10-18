"""
When a UI submit may be clicked again, and with what.

A write is resent only when the server provably did not act on the first one:
the request was never sent, or it was refused with a 4xx. Everything else may
have acted — a 2xx did, and a 5xx, a gateway timeout or no answer at all may have
written before failing — so resending could write twice. The same rule decides
whether a step may retry with a *different* value (another reason code): only
after a refusal, never after an answer that may have been applied.

``submit_tracking_sent`` records whether the click actually sent the call, which
``SubmitResult`` alone cannot tell (a missing response looks the same either way).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from ui.components.submit_outcome import SubmitResult, submit_and_capture


def server_did_not_act(sent: bool, status: int | None) -> bool:
    """True only when resending cannot duplicate a write: never sent, or refused (4xx)."""
    if not sent:
        return True
    return status is not None and 400 <= status < 500


@dataclass(frozen=True)
class TrackedSubmit:
    sent: bool
    outcome: SubmitResult

    @property
    def may_resend(self) -> bool:
        return server_did_not_act(self.sent, self.outcome.status)


def submit_tracking_sent(
    page,
    click: Callable[[], None],
    url: str | re.Pattern[str],
    *,
    method: str = "POST",
    **kwargs,
) -> TrackedSubmit:
    """``submit_and_capture`` plus whether a matching request left the browser at all."""
    sent: list[str] = []

    def matches(request_url: str) -> bool:
        return (
            url.search(request_url) is not None
            if isinstance(url, re.Pattern)
            else url in request_url
        )

    def on_request(request) -> None:
        if request.method.upper() == method.upper() and matches(request.url):
            sent.append(request.url)

    page.on("request", on_request)
    try:
        outcome = submit_and_capture(page, click, url, method=method, **kwargs)
    finally:
        page.remove_listener("request", on_request)
    return TrackedSubmit(bool(sent), outcome)


def next_choice(
    preferred: Sequence[str], offered: Iterable[str], tried: Iterable[str]
) -> str | None:
    """The first preferred value the screen offers that was not tried yet, else None."""
    available = set(offered)
    done = set(tried)
    for value in preferred:
        if value in available and value not in done:
            return value
    return None
