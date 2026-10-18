"""Gateway route delta: a route reaches its service, and its control is the gateway's own 404.

A 404 alone cannot say whether a gateway route exists. A forwarded route answers an unknown
id with the *service's* not-found body; a path no route matches gets the *gateway's*
no-route body. ``api.clients.route_probe.classify_not_found`` tells them apart by body
shape, and each route is paired with a control path its predicate must reject, so a
classifier that always says "service" cannot pass.
"""

from __future__ import annotations

import pytest

from api.clients.http_engine import HttpEngine
from api.clients.route_probe import (
    NotFoundShapes,
    RouteVerdict,
    classify_not_found,
    require_measured,
)

#: The demo's two 404 bodies: {"error": "no_route", "source", "path"} vs {"resource", "id"}.
DEMO_SHAPES = NotFoundShapes(
    gateway_keys=frozenset({"source", "path"}),
    error_key="error",
    error_text="no_route",
    service_keys=frozenset({"resource", "id"}),
)
ROUTES = ["jobs", "documents", "invoices", "messages", "customers", "subscriptions"]


@pytest.fixture
def gateway(demo_sut, demo_control):
    return HttpEngine(demo_sut.base_url + "/", max_retries=0)


@pytest.mark.parametrize("area", ROUTES)
def test_new_route_reaches_service_not_gateway_404(gateway, area):
    reached = require_measured(gateway.get(f"gw/{area}/999999"))
    assert classify_not_found(reached, DEMO_SHAPES) is RouteVerdict.SERVICE_NOT_FOUND, (
        f"/gw/{area}/<unknown id> did not reach the service: {reached} {reached.text[:200]}"
    )
    assert reached.header("X-Demo-Gateway") == "forwarded"

    control = require_measured(gateway.get(f"gw/{area}x/999999"))
    assert classify_not_found(control, DEMO_SHAPES) is RouteVerdict.GATEWAY_NO_ROUTE, (
        f"control /gw/{area}x/... was not rejected by the gateway: {control.text[:200]}"
    )
    assert control.header("X-Demo-Source") == "gateway"


def test_a_known_record_through_the_route_is_not_a_404(gateway, demo_seed):
    sub = demo_seed.subscription(state="draft")
    response = gateway.get(f"gw/subscriptions/{sub['id']}")
    assert response.status == 200 and response.json()["id"] == sub["id"]
    assert classify_not_found(response, DEMO_SHAPES) is RouteVerdict.NOT_A_404
