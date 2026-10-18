"""Environment circuit breaker: what trips it, what is exempt, and run-wide abstention."""

from __future__ import annotations

import json
import threading

import pytest

from gate.circuit_breaker import (
    ServerErrorBreaker,
    assume_environment_not_tripped,
    install_default,
    problem_detail_upstream_timeout,
)

API = "https://api.example.test"
UI = "https://app.example.test"


def _breaker(**kw) -> ServerErrorBreaker:
    return ServerErrorBreaker([f"{API}/v1/", UI], **kw)


def _upstream_body(detail: str) -> str:
    return json.dumps({"type": "about:blank", "title": "Operation failed", "detail": detail})


READ_TIMEOUT = _upstream_body(
    'upstream call to "https://lookup.example.test/lookups/address-check": Read timed out'
)

CONNECT_TIMEOUT = _upstream_body(
    'upstream call to "https://lookup.example.test/lookups/x": Connect timed out'
)
OTHER_DEPENDENCY = _upstream_body('upstream call to "https://other.example.test/x": Read timed out')


def test_a_5xx_from_the_environment_trips_and_keeps_the_first_reason():
    breaker = _breaker()
    breaker.record("api", "GET", f"{API}/v1/orders", 503)
    breaker.record("browser", "GET", f"{UI}/home", 500)
    reason = breaker.tripped_reason()
    assert reason is not None and "/v1/orders -> 503" in reason


@pytest.mark.parametrize("status", [200, 302, 404, 499])
def test_non_5xx_does_not_trip(status):
    breaker = _breaker()
    breaker.record("api", "GET", f"{API}/x", status)
    assert breaker.tripped_reason() is None


def test_foreign_host_and_bad_urls_do_not_trip():
    breaker = _breaker()
    breaker.record("browser", "GET", "https://cdn.other.test/lib.js", 503)
    breaker.record("browser", "GET", "not a url", 500)
    breaker.record("browser", "GET", "", 500)
    assert breaker.tripped_reason() is None


def test_disabled_breaker_never_trips():
    breaker = _breaker(enabled=False)
    breaker.record("api", "GET", f"{API}/x", 500)
    assert breaker.tripped_reason() is None


def test_declared_expected_error_is_exempt_only_inside_its_scope():
    breaker = _breaker()
    with breaker.expect_server_error("/orders/submit", "provokes a 500 on purpose") as scope:
        breaker.record("api", "POST", f"{API}/orders/submit", 500)
        assert breaker.tripped_reason() is None
        assert scope.exempted == 1
    breaker.record("api", "POST", f"{API}/orders/submit", 500)
    assert breaker.tripped_reason() is not None


def test_expected_error_scope_does_not_cover_other_calls():
    breaker = _breaker()
    with breaker.expect_server_error("/orders/submit", "on purpose"):
        breaker.record("api", "GET", f"{API}/orders/list", 500)
    assert breaker.tripped_reason() is not None


def test_expected_error_scope_is_thread_bound():
    breaker = _breaker()
    with breaker.expect_server_error("/orders/submit", "on purpose"):
        worker = threading.Thread(
            target=breaker.record, args=("api", "POST", f"{API}/orders/submit", 500)
        )
        worker.start()
        worker.join()
    assert breaker.tripped_reason() is not None


@pytest.mark.parametrize(("fragment", "reason"), [("", "why"), ("  ", "why"), ("/x", "")])
def test_blank_exemption_is_refused(fragment, reason):
    with pytest.raises(ValueError, match="empty fragment"):
        _breaker().expect_server_error(fragment, reason)


def test_upstream_read_timeouts_are_exempt_up_to_the_limit():
    breaker = _breaker(upstream_timeout=problem_detail_upstream_timeout("/lookups/"))
    for _ in range(3):
        breaker.record("api", "POST", f"{API}/v1/orders/1/submit", 500, READ_TIMEOUT)
    assert breaker.tripped_reason() is None
    breaker.record("api", "POST", f"{API}/v1/orders/1/submit", 500, READ_TIMEOUT)
    reason = breaker.tripped_reason()
    assert reason is not None and "number 4, over the limit of 3" in reason
    assert "lookup.example.test/lookups/address-check" in reason


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (502, READ_TIMEOUT),  # gateway errors are never exempt
        (504, READ_TIMEOUT),
        (500, CONNECT_TIMEOUT),  # not reaching the dependency is the environment failing
        (500, "<html>Internal error</html>"),
        (500, OTHER_DEPENDENCY),
        (500, json.dumps(["not", "an", "object"])),
        (500, None),
    ],
)
def test_everything_else_still_trips(status, body):
    breaker = _breaker(upstream_timeout=problem_detail_upstream_timeout("/lookups/"))
    breaker.record("api", "POST", f"{API}/v1/orders/1/submit", status, body)
    assert breaker.tripped_reason() is not None


def test_trip_is_shared_through_the_trip_file(tmp_path):
    trip = tmp_path / "run.tripped"
    first, second = _breaker(trip_file=trip), _breaker(trip_file=trip)
    first.record("api", "GET", f"{API}/x", 503)
    third = _breaker(trip_file=trip)
    third.record("api", "GET", f"{API}/y", 500)
    # The first trip's reason is the one every process reports.
    assert "/x -> 503" in second.tripped_reason()
    assert "/x -> 503" in trip.read_text()


def test_assume_not_tripped_skips_with_the_reason():
    breaker = install_default(_breaker())
    assume_environment_not_tripped()
    breaker.record("api", "GET", f"{API}/x", 502)
    with pytest.raises(pytest.skip.Exception, match="already returned a 5xx in this run"):
        assume_environment_not_tripped()
    breaker.reset()
    assume_environment_not_tripped()


def test_from_env(monkeypatch):
    monkeypatch.setenv("CIRCUIT_BREAKER_HOSTS", "api.example.test, app.example.test")
    monkeypatch.setenv("CIRCUIT_BREAKER_UPSTREAM_LIMIT", "x")
    breaker = ServerErrorBreaker.from_env(fallback_hosts=["https://ignored.test"])
    assert breaker.hosts == {"api.example.test", "app.example.test"}
    assert breaker.upstream_limit == 3
    monkeypatch.delenv("CIRCUIT_BREAKER_HOSTS")
    assert ServerErrorBreaker.from_env(fallback_hosts=[UI]).hosts == {"app.example.test"}
