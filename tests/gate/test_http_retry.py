"""Gateway-error retry policy: what may be re-sent, and when the run abstains."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gate.http_retry import (
    AfterFailure,
    Decision,
    RetryPolicy,
    fetch_with_retry,
    versioned_write,
)

URL = "https://api.example.test/v1/orders/7/submit"


@pytest.mark.parametrize(
    ("method", "url", "expected"),
    [
        ("POST", f"{URL}?version=3", True),
        ("PUT", f"{URL}?a=1&version=12", True),
        ("POST", f"{URL}?version=", False),
        ("POST", URL, False),
        ("GET", f"{URL}?version=3", False),  # a read is not a write at all
    ],
)
def test_versioned_write(method, url, expected):
    assert versioned_write(method, url) is expected


def test_500_is_never_retried():
    policy = RetryPolicy()
    assert policy.decide("GET", URL, 500, 0) is Decision.DELIVER


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_errors_retry_on_reads_and_deduplicated_writes(status):
    policy = RetryPolicy()
    assert policy.decide("GET", URL, status, 0) is Decision.RETRY
    assert policy.decide("POST", f"{URL}?version=2", status, 0) is Decision.RETRY
    assert policy.decide("POST", URL, status, 0) is Decision.DELIVER


def test_gateway_error_after_the_last_retry_abstains():
    policy = RetryPolicy(max_retries=2)
    assert policy.decide("GET", URL, 503, 2) is Decision.ABSTAIN


def test_protects_skips_streaming_and_respects_a_narrower_predicate():
    policy = RetryPolicy()
    assert policy.protects("GET", "https://api.example.test/v1/files/1/download") is False
    assert policy.protects("GET", "https://api.example.test/v1/events") is False
    assert policy.protects("GET", URL) is True
    assert policy.protects("POST", URL) is False
    narrow = RetryPolicy(protects_predicate=lambda m, u: "/configurations" in u)
    assert narrow.protects("GET", URL) is False
    assert RetryPolicy(max_retries=0).protects("GET", URL) is False


def test_after_fetch_failure_never_resends_a_write():
    assert RetryPolicy.after_fetch_failure("GET") is AfterFailure.RESUME
    assert RetryPolicy.after_fetch_failure("post") is AfterFailure.ABORT


def _fetcher(*statuses):
    queue = list(statuses)
    calls = []

    def fetch():
        calls.append(1)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(status=item)

    return fetch, calls


def test_fetch_with_retry_recovers_with_linear_backoff():
    fetch, calls = _fetcher(503, 502, 200)
    sleeps, discarded = [], []
    outcome = fetch_with_retry(
        fetch,
        "GET",
        URL,
        RetryPolicy(backoff_s=0.5),
        on_discard=lambda attempt, status: discarded.append((attempt, status)),
        sleep=sleeps.append,
    )
    assert (outcome.status, outcome.retries, outcome.decision) == (200, 2, Decision.DELIVER)
    assert sleeps == [0.5, 1.0]
    assert discarded == [(1, 503), (2, 502)]
    assert len(calls) == 3


def test_fetch_with_retry_abstains_when_retries_run_out():
    fetch, calls = _fetcher(504, 504, 504)
    outcome = fetch_with_retry(fetch, "GET", URL, RetryPolicy(max_retries=2), sleep=lambda s: None)
    assert (outcome.status, outcome.retries, outcome.decision) == (504, 2, Decision.ABSTAIN)
    assert len(calls) == 3


def test_unsafe_write_is_sent_once():
    fetch, calls = _fetcher(503)
    outcome = fetch_with_retry(fetch, "POST", URL, RetryPolicy(), sleep=lambda s: None)
    assert (outcome.decision, len(calls)) == (Decision.DELIVER, 1)


def test_a_failing_refetch_ends_the_loop_with_the_last_response():
    fetch, _ = _fetcher(503, ConnectionError("reset"))
    outcome = fetch_with_retry(fetch, "GET", URL, RetryPolicy(), sleep=lambda s: None)
    assert (outcome.status, outcome.retries, outcome.decision) == (503, 1, Decision.ABSTAIN)


def test_the_first_fetch_is_not_guarded():
    fetch, _ = _fetcher(ConnectionError("down"))
    with pytest.raises(ConnectionError):
        fetch_with_retry(fetch, "GET", URL, RetryPolicy(), sleep=lambda s: None)


def test_from_env(monkeypatch):
    monkeypatch.setenv("HTTP_RETRY_5XX", "0")
    monkeypatch.setenv("HTTP_RETRY_BACKOFF_S", "junk")
    policy = RetryPolicy.from_env()
    assert (policy.max_retries, policy.backoff_s) == (0, 1.5)
