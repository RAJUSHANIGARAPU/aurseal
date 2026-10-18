"""Fault injection offline: spec matching and windows, the API transport, the breaker rule,
and the ``held_empty`` control. No browser, no network."""

from __future__ import annotations

import pytest
import requests

from api.clients.http_engine import HttpEngine
from checks.faults.api import FaultyTransport
from checks.faults.oracles import OracleResult, recovers_after, retries_bounded
from checks.faults.spec import HEADER, FaultKind, FaultPlan, FaultSpec, glob_to_regex, is_injected
from gate import circuit_breaker
from gate.circuit_breaker import ServerErrorBreaker

pytest_plugins = ["pytester"]

URL = "http://svc.test/api/orders/7"


class TestSpec:
    @pytest.mark.parametrize(
        ("glob", "url", "hit"),
        [
            ("**/api/orders/*", URL, True),
            ("**/api/orders/*", "http://svc.test/api/orders/7/lines", False),
            ("**/api/**", "http://svc.test/api/orders/7/lines", True),
            ("http://svc.test/api/orders/?", "http://svc.test/api/orders/7", False),
            ("**/health", URL, False),
        ],
    )
    def test_glob(self, glob, url, hit):
        assert bool(glob_to_regex(glob).match(url)) is hit

    def test_method_filter(self):
        spec = FaultSpec("status", "**/orders/*", method="post")
        assert spec.matches("POST", URL) and not spec.matches("GET", URL)

    def test_after_n_and_times_window(self):
        plan = FaultPlan((FaultSpec("status", "**", after_n=2, times=2),))
        fired = [plan.decide("GET", URL) is not None for _ in range(6)]
        assert fired == [False, False, True, True, False, False]
        assert plan.seen() == 6 and plan.fired() == 2

    def test_seeded_probability_replays(self):
        def run(seed):
            plan = FaultPlan((FaultSpec("abort", "**", probability=0.5),), seed=seed)
            return [plan.decide("GET", URL) is not None for _ in range(40)]

        assert run(7) == run(7)
        assert run(7) != run(8)
        assert 5 < sum(run(7)) < 35

    def test_each_spec_counts_its_own_window(self):
        first = FaultSpec("status", "**", times=1, id="a")
        second = FaultSpec("abort", "**", after_n=1, id="b")
        plan = FaultPlan((first, second))
        assert [getattr(plan.decide("GET", URL), "id", None) for _ in range(3)] == ["a", "b", "b"]

    def test_inactive_plan_never_fires(self):
        plan = FaultPlan((FaultSpec("status", "**"),))
        plan.active = False
        assert plan.decide("GET", URL) is None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"kind": "explode", "url_glob": "**"},
            {"kind": "status", "url_glob": ""},
            {"kind": "status", "url_glob": "**", "probability": 2},
            {"kind": "status", "url_glob": "**", "status": 99},
            {"kind": "latency", "url_glob": "**"},
            {"kind": "status", "url_glob": "**", "after_n": -1},
        ],
    )
    def test_invalid_specs(self, kwargs):
        with pytest.raises(ValueError):
            FaultSpec(**kwargs)

    def test_duplicate_ids_are_refused(self):
        with pytest.raises(ValueError):
            FaultPlan((FaultSpec("status", "**", id="x"), FaultSpec("abort", "**", id="x")))

    def test_from_dict_and_default_id(self):
        spec = FaultSpec.from_dict({"kind": "status", "url_glob": "**/x", "method": "GET"})
        assert spec.kind is FaultKind.STATUS and spec.id == "status:GET:**/x"

    def test_is_injected(self):
        assert is_injected({"X-Fault-Injected": "a"}) and not is_injected({"x": "1"})
        assert not is_injected(None)


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.headers: dict[str, str] = {}

    def request(self, method, url, **kw):  # noqa: ANN001, ANN003
        self.calls.append((method, url))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok": true, "items": [1, 2, 3, 4]}'
        response.url = url
        response.headers["Content-Type"] = "application/json"
        return response


class TestApiTransport:
    def test_status_fault_is_not_sent_and_is_marked(self):
        inner = FakeSession()
        transport = FaultyTransport(inner, [FaultSpec("status", "**/orders/*", status=503)])
        response = transport.request("GET", URL)
        assert response.status_code == 503 and response.headers[HEADER]
        assert inner.calls == [] and transport.fired() == 1

    def test_abort_and_timeout_raise_the_requests_errors(self):
        transport = FaultyTransport(
            FakeSession(), [FaultSpec("abort", "**", times=1), FaultSpec("timeout", "**")]
        )
        with pytest.raises(requests.ConnectionError):
            transport.request("GET", URL)
        with pytest.raises(requests.Timeout):
            transport.request("GET", URL)

    def test_latency_waits_then_sends(self):
        slept: list[float] = []
        inner = FakeSession()
        transport = FaultyTransport(
            inner, [FaultSpec("latency", "**", latency_ms=250)], sleep=slept.append
        )
        response = transport.request("GET", URL)
        assert slept == [0.25] and response.status_code == 200 and len(inner.calls) == 1
        assert response.headers[HEADER]

    def test_truncated_body_is_half_of_the_real_one(self):
        transport = FaultyTransport(FakeSession(), [FaultSpec("truncate_body", "**")])
        response = transport.request("GET", URL)
        assert len(response.content) == len(b'{"ok": true, "items": [1, 2, 3, 4]}') // 2
        with pytest.raises(ValueError):
            response.json()

    def test_unmatched_requests_pass_through_and_remove_stops_it(self):
        inner = FakeSession()
        transport = FaultyTransport(inner, [FaultSpec("status", "**/payments/*")])
        assert transport.request("GET", URL).status_code == 200
        transport = FaultyTransport(inner, [FaultSpec("status", "**")])
        transport.remove()
        assert transport.request("GET", URL).status_code == 200 and transport.fired() == 0

    def test_it_drops_into_the_http_engine(self):
        transport = FaultyTransport(FakeSession(), [FaultSpec("status", "**", times=1)])
        engine = HttpEngine("http://svc.test/api/", session=transport)
        first = engine.get("orders/7")
        second = engine.get("orders/7")
        assert first.status == 503 and first.header(HEADER)
        assert second.status == 200

    def test_oracles(self):
        transport = FaultyTransport(FakeSession(), [FaultSpec("status", "**", id="s")])
        transport.request("GET", URL)
        assert retries_bounded(transport, 1, "s").passed
        transport.request("GET", URL)
        assert not retries_bounded(transport, 1, "s").passed
        result = recovers_after(
            transport,
            lambda: None,
            lambda: OracleResult("ok", transport.request("GET", URL).status_code == 200),
        )
        assert result.passed
        with pytest.raises(AssertionError):
            OracleResult("x", False, "why").check()


class TestBreakerIgnoresInjected:
    def test_the_header_names_agree(self):
        assert circuit_breaker.FAULT_INJECTED_HEADER == HEADER

    def test_an_injected_503_does_not_trip(self):
        breaker = ServerErrorBreaker(["svc.test"])
        breaker.record("api", "GET", URL, 503, headers={HEADER: "status:*:**"})
        assert breaker.tripped_reason() is None

    def test_a_real_503_still_trips(self):
        """Positive control: the exemption is the header, not the status."""
        breaker = ServerErrorBreaker(["svc.test"])
        breaker.record("api", "GET", URL, 503, headers={"content-type": "application/json"})
        assert breaker.tripped_reason() is not None


CONFTEST = """
from checks.faults.pytest_plugin import faults, pytest_configure, pytest_runtest_makereport
"""

TESTS = """
import pytest, requests
from checks.faults.spec import FaultSpec


class Session:
    def request(self, method, url, **kw):
        r = requests.Response(); r.status_code = 200; r._content = b"{}"; return r


@pytest.mark.chaos
def test_fault_fires(faults):
    t = faults.api(Session(), [FaultSpec("status", "**/orders")])
    assert t.request("GET", "http://x.test/orders").status_code == 503


@pytest.mark.chaos
def test_fault_never_fires(faults):
    t = faults.api(Session(), [FaultSpec("status", "**/payments")])
    assert t.request("GET", "http://x.test/orders").status_code == 200


@pytest.mark.chaos
def test_fault_fires_and_test_fails(faults):
    t = faults.api(Session(), [FaultSpec("status", "**")])
    assert t.request("GET", "http://x.test/a").status_code == 200
"""


def test_a_test_whose_fault_never_fired_is_not_a_pass(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_faults=TESTS)
    result = pytester.runpytest("-o", "addopts=", "-p", "no:cacheprovider", "-rs")
    result.assert_outcomes(passed=1, skipped=1, failed=1)
    result.stdout.fnmatch_lines(["*held_empty*no injected fault fired*"])
    rows = (tmp_path / "metrics" / "metric_fault_injection.jsonl").read_text().splitlines()
    assert len(rows) == 3
