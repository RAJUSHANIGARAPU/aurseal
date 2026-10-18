"""Tests for api.clients.route_probe."""

from __future__ import annotations

import json

import pytest

from api.clients.http_engine import ApiResponse, HttpEngine
from api.clients.route_probe import (
    NotFoundShapes,
    RouteVerdict,
    UnmeasuredResponse,
    classify_not_found,
    is_gateway_no_route,
    is_measured,
    require_measured,
)
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

GATEWAY_BODY = {
    "timestamp": "2031-05-04T10:00:00Z",
    "path": "/api/orders/not-an-id",
    "status": 404,
    "error": "Not Found",
    "requestId": "a1b2c3",
}
SERVICE_BODY = {
    "title": "Not Found",
    "detail": "Order ORD-42 not found",
    "instance": "/api/orders/ORD-42",
    "status": 404,
}


def _r(status: int, body: object = b"") -> ApiResponse:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return ApiResponse(status, raw)


class TestClassifyNotFound:
    def test_gateway_default_body_is_no_route(self):
        assert classify_not_found(_r(404, GATEWAY_BODY)) is RouteVerdict.GATEWAY_NO_ROUTE
        assert is_gateway_no_route(_r(404, GATEWAY_BODY))

    def test_problem_detail_is_a_service_answer(self):
        assert classify_not_found(_r(404, SERVICE_BODY)) is RouteVerdict.SERVICE_NOT_FOUND
        assert not is_gateway_no_route(_r(404, SERVICE_BODY))

    def test_gateway_keys_plus_title_counts_as_service(self):
        body = {**GATEWAY_BODY, "title": "Order missing"}
        assert classify_not_found(_r(404, body)) is RouteVerdict.SERVICE_NOT_FOUND

    def test_other_status_is_not_judged(self):
        assert classify_not_found(_r(200, GATEWAY_BODY)) is RouteVerdict.NOT_A_404

    @pytest.mark.parametrize(
        "body",
        [b"", b"   ", b"<html>gone</html>", b"[1, 2]", {"message": "nope"}],
    )
    def test_unknown_bodies_are_unrecognised(self, body):
        assert classify_not_found(_r(404, body)) is RouteVerdict.UNRECOGNISED

    def test_gateway_shape_needs_the_error_text(self):
        body = {**GATEWAY_BODY, "error": "Gone"}
        assert classify_not_found(_r(404, body)) is RouteVerdict.UNRECOGNISED

    def test_null_gateway_key_does_not_count(self):
        body = {**GATEWAY_BODY, "requestId": None}
        assert classify_not_found(_r(404, body)) is RouteVerdict.UNRECOGNISED

    def test_shapes_are_configurable(self):
        shapes = NotFoundShapes(
            gateway_keys=frozenset({"trace"}),
            error_key="code",
            error_text="NO_ROUTE",
            service_keys=frozenset({"reason"}),
        )
        assert (
            classify_not_found(_r(404, {"trace": "t1", "code": "NO_ROUTE"}), shapes)
            is RouteVerdict.GATEWAY_NO_ROUTE
        )
        assert (
            classify_not_found(_r(404, {"reason": "missing"}), shapes)
            is RouteVerdict.SERVICE_NOT_FOUND
        )
        # the default gateway body is not a gateway answer under other shapes
        assert classify_not_found(_r(404, GATEWAY_BODY), shapes) is RouteVerdict.UNRECOGNISED

    def test_error_text_none_skips_the_text_check(self):
        shapes = NotFoundShapes(error_text=None)
        body = {**GATEWAY_BODY, "error": "anything"}
        assert classify_not_found(_r(404, body), shapes) is RouteVerdict.GATEWAY_NO_ROUTE


class TestRequireMeasured:
    def test_401_is_not_measured(self):
        assert not is_measured(_r(401, {"error": "unauthorized"}))
        with pytest.raises(UnmeasuredResponse) as exc:
            require_measured(_r(401))
        assert exc.value.response.status == 401
        assert "routing stays unknown" in str(exc.value)

    def test_plain_text_403_is_not_measured(self):
        with pytest.raises(UnmeasuredResponse):
            require_measured(_r(403, b"Access Denied"))

    def test_json_403_is_a_measured_answer(self):
        r = _r(403, {"title": "Forbidden"})
        assert require_measured(r) is r

    @pytest.mark.parametrize("status", [200, 404, 500])
    def test_other_statuses_are_measured(self, status):
        assert is_measured(_r(status, b"x"))


def test_end_to_end_over_the_engine():
    adapter = (
        FakeAdapter()
        .on("GET", "/api/orders/ORD-42", make_response(404, SERVICE_BODY))
        .on("GET", "/api/nowhere", make_response(404, GATEWAY_BODY))
    )
    api = HttpEngine("https://gw.example.test/", session=session_with(adapter))
    missing = require_measured(api.get("/api/orders/ORD-42"))
    unrouted = require_measured(api.get("/api/nowhere"))
    assert classify_not_found(missing) is RouteVerdict.SERVICE_NOT_FOUND
    assert classify_not_found(unrouted) is RouteVerdict.GATEWAY_NO_ROUTE
