"""Write-route probe on the demo gateway: the service refuses, nothing is created.

``POST /gw/tasks`` with an empty body must reach the task service and be refused by the
service's own validation (422 naming ``title``) - the request never changes anything. The
negative control is a path outside the gateway's route predicate, answered by the gateway's
own 404; the positive control is a read on the same route.
"""

from __future__ import annotations

from api.clients.http_engine import HttpEngine
from api.clients.route_probe import WriteVerdict, probe_write, service_refusal
from tests.demo_examples.smoke.test_gateway_routes import DEMO_SHAPES

TITLE_REQUIRED = service_refusal({422}, keys=("fields",), body_contains=("title",))


def test_post_route_reaches_the_task_service_without_creating(demo_sut, demo_control):
    gateway = HttpEngine(demo_sut.base_url + "/", max_retries=0)
    before = len(gateway.get("gw/tasks").json())

    result = probe_write(
        gateway,
        "POST",
        "gw/tasks",
        {},
        control=("POST", "gw/tasksx"),
        positive="gw/tasks",
        accept=TITLE_REQUIRED,
        shapes=DEMO_SHAPES,
        read_only=False,
    ).require()

    assert result.verdict is WriteVerdict.REACHES_SERVICE
    assert result.refusal.header("X-Demo-Source") == "service"
    assert result.control.header("X-Demo-Source") == "gateway"
    assert len(gateway.get("gw/tasks").json()) == before, "the probe must not create a task"


def test_a_probe_the_service_accepts_is_reported_as_a_side_effect(demo_sut, demo_control):
    gateway = HttpEngine(demo_sut.base_url + "/", max_retries=0)
    result = probe_write(
        gateway,
        "POST",
        "gw/tasks",
        {"title": "Probe that should have been refused"},
        control=("POST", "gw/tasksx"),
        positive="gw/tasks",
        accept=TITLE_REQUIRED,
        shapes=DEMO_SHAPES,
        read_only=False,
    )
    assert result.verdict is WriteVerdict.SIDE_EFFECT and not result.passed
