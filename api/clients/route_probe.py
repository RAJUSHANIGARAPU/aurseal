"""Tell a gateway's own "no route" 404 apart from a 404 a service behind it returned.

Each case comes back as 404; the status code by itself cannot tell them apart.
The difference is in the body: a gateway answers an unmatched path with its
default error object (typically ``{timestamp, path, status, error, requestId}``),
a service answers a missing resource with a problem detail
(``{title, detail, instance, status}``). :func:`classify_not_found` reads the
body shape; the key sets are configurable through :class:`NotFoundShapes`
because every gateway words its default error a little differently.

:func:`require_measured` guards the probe itself: a 401, or a plain-text 403
from a firewall in front of the gateway, means the request was never routed,
and labelling it "route present" or "route missing" would turn an auth failure
into a false product result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from api.clients.http_engine import ApiResponse


class RouteVerdict(str, Enum):
    NOT_A_404 = "not-a-404"
    """The status is not 404; this classifier has nothing to say."""
    GATEWAY_NO_ROUTE = "gateway-no-route"
    """The gateway's own body: no route matched the path."""
    SERVICE_NOT_FOUND = "service-not-found"
    """A service answered: the route exists, the resource does not."""
    UNRECOGNISED = "unrecognised"
    """A 404 whose body matches neither shape (empty, not JSON, or another layout)."""


@dataclass(frozen=True)
class NotFoundShapes:
    """The body keys that identify each 404 shape.

    A body is the gateway's when it has every key in ``gateway_keys`` (non-null),
    its ``error_key`` equals ``error_text`` (when ``error_text`` is set), and it
    has none of ``service_keys``. A body is a service's when it has every key in
    ``service_keys``.
    """

    gateway_keys: frozenset[str] = frozenset({"requestId", "path"})
    error_key: str = "error"
    error_text: str | None = "Not Found"
    service_keys: frozenset[str] = frozenset({"title"})


DEFAULT_SHAPES = NotFoundShapes()


class UnmeasuredResponse(AssertionError):
    """The request was not authenticated, so the response routing stays unknown."""

    def __init__(self, response: ApiResponse) -> None:
        super().__init__(
            "the call was rejected as unauthenticated, so routing stays unknown: " + str(response)
        )
        self.response = response


def _json_object(body: str) -> Mapping[str, Any] | None:
    if not body.strip():
        return None
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _has_all(obj: Mapping[str, Any], keys: frozenset[str]) -> bool:
    return all(obj.get(k) is not None for k in keys)


def classify_not_found(
    response: ApiResponse, shapes: NotFoundShapes = DEFAULT_SHAPES
) -> RouteVerdict:
    """Which kind of 404 ``response`` is, judged by its body shape."""
    if response.status != 404:
        return RouteVerdict.NOT_A_404
    body = _json_object(response.text)
    if body is None:
        return RouteVerdict.UNRECOGNISED
    if shapes.service_keys and _has_all(body, shapes.service_keys):
        return RouteVerdict.SERVICE_NOT_FOUND
    if (
        _has_all(body, shapes.gateway_keys)
        and (shapes.error_text is None or body.get(shapes.error_key) == shapes.error_text)
        and not any(k in body for k in shapes.service_keys)
    ):
        return RouteVerdict.GATEWAY_NO_ROUTE
    return RouteVerdict.UNRECOGNISED


def is_gateway_no_route(response: ApiResponse, shapes: NotFoundShapes = DEFAULT_SHAPES) -> bool:
    """Whether ``response`` is the gateway's own no-route 404."""
    return classify_not_found(response, shapes) is RouteVerdict.GATEWAY_NO_ROUTE


def is_measured(response: ApiResponse) -> bool:
    """False for a 401, and for a 403 whose body is not JSON (a firewall's plain-text refusal)."""
    if response.status == 401:
        return False
    return response.status != 403 or response.text.lstrip().startswith("{")


def require_measured(response: ApiResponse) -> ApiResponse:
    """Return ``response`` when it says something about routing; raise when it cannot."""
    if not is_measured(response):
        raise UnmeasuredResponse(response)
    return response
