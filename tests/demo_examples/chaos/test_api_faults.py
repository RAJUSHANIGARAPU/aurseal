"""API-side fault injection against the demo service (offline: localhost only).

The project's ``HttpEngine`` runs on a ``FaultyTransport``: an injected 503 is marked and
does not trip the circuit breaker, a real 503 from the demo's ``api_unavailable`` switch is
not marked and does (the control), an aborted call raises, and the flow recovers once the
fault window is over.
"""

from __future__ import annotations

import pytest
import requests

from api.clients.http_engine import HttpEngine
from checks.faults.oracles import recovers_after, retries_bounded
from checks.faults.spec import HEADER, FaultSpec
from gate.circuit_breaker import ServerErrorBreaker

pytestmark = pytest.mark.chaos


def _engine(demo_sut, transport) -> HttpEngine:
    return HttpEngine(demo_sut.api_url, session=transport, max_retries=0)


def test_injected_503_is_marked_and_does_not_trip_the_breaker(demo_sut, demo_control, faults):
    demo_control.faults()
    transport = faults.api(requests.Session(), [FaultSpec("status", "**/api/plans", status=503)])
    breaker = ServerErrorBreaker([demo_sut.base_url])
    response = _engine(demo_sut, transport).get("plans")
    breaker.record(
        "api", "GET", demo_sut.api_url + "plans", response.status, headers=response.headers
    )
    assert response.status == 503 and response.header(HEADER)
    assert breaker.tripped_reason() is None


def test_a_real_503_is_not_marked_and_trips_the_breaker(demo_sut, demo_control):
    """Control: the demo itself failing (no injection at all) must still trip the breaker."""
    demo_control.fault("api_unavailable")
    breaker = ServerErrorBreaker([demo_sut.base_url])
    response = HttpEngine(demo_sut.api_url, max_retries=0).get("plans")
    breaker.record(
        "api", "GET", demo_sut.api_url + "plans", response.status, headers=response.headers
    )
    assert response.status == 503 and response.header(HEADER) is None
    assert breaker.tripped_reason() is not None
    assert demo_control.evidence() == "api_unavailable"


def test_an_aborted_call_raises_and_the_next_one_recovers(demo_sut, demo_control, faults):
    demo_control.faults()
    transport = faults.api(requests.Session(), [FaultSpec("abort", "**/api/plans", times=1)])
    engine = _engine(demo_sut, transport)
    with pytest.raises(requests.ConnectionError):
        engine.get("plans")
    assert engine.get("plans").status == 200
    assert retries_bounded(transport, 2).passed


def test_latency_is_applied_and_the_answer_is_still_right(demo_sut, demo_control, faults):
    demo_control.faults()
    transport = faults.api(
        requests.Session(), [FaultSpec("latency", "**/api/plans", latency_ms=300)]
    )
    response = _engine(demo_sut, transport).get("plans")
    assert response.status == 200 and response.header(HEADER)
    assert response.json()


def test_the_flow_recovers_once_the_fault_is_removed(demo_sut, demo_control, faults):
    demo_control.faults()
    transport = faults.api(requests.Session(), [FaultSpec("status", "**/api/plans")])
    engine = _engine(demo_sut, transport)
    assert engine.get("plans").status == 503
    result = recovers_after(transport, lambda: None, lambda: engine.get("plans").status == 200)
    assert faults.note(result).passed, result.detail
