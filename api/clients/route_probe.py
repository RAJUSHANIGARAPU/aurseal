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

Write routes are proven the same way, without side effects
(:func:`probe_write`): the probe sends a request the service must refuse
before changing anything (a random id, an invalid body) and requires the
service's *own* refusal (``accept``: a status set and a body check - a problem
detail naming the resource or its required fields, or its permission
refusal). Two controls go with it: a negative one outside the route predicate
(another method, a malformed path) that the gateway must answer itself, and a
positive one on a known routed path that must answer 2xx. A probe that is
*accepted* changed something; that is reported as such, never as a pass.

A body-less method (HEAD) cannot be refused by body, so :func:`probe_head`
proves it on a resource the test created: 200 while it exists, 404 after it is
discarded. Both skip on a read-only environment
(:func:`gate.read_only.require_writable`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

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


# ---------------------------------------------------------------------------
# write routes and HEAD
# ---------------------------------------------------------------------------


class WriteVerdict(str, Enum):
    REACHES_SERVICE = "reaches-service"
    """The service refused the probe with its own refusal; both controls held."""
    NOT_ROUTED = "not-routed"
    """The gateway answered the probe itself: no route forwards it."""
    SIDE_EFFECT = "side-effect"
    """The probe was accepted (2xx): it changed something. Never a pass."""
    UNEXPECTED_REFUSAL = "unexpected-refusal"
    """Refused, but not with the service's refusal ``accept`` describes."""
    CONTROL_NOT_GATEWAY = "control-not-gateway"
    """The negative control was not answered by the gateway, so 'routed' proves nothing."""
    POSITIVE_FAILED = "positive-failed"
    """The known routed path did not answer 2xx; the gateway itself may be down."""


Accept = Callable[[ApiResponse], bool]


def service_refusal(
    statuses: Collection[int], *, body_contains: tuple[str, ...] = (), keys: tuple[str, ...] = ()
) -> Accept:
    """An ``accept`` predicate: status in ``statuses``, the JSON body carries every key in
    ``keys``, and the text carries every fragment in ``body_contains`` (case-insensitive)."""
    wanted = frozenset(statuses)

    def accept(response: ApiResponse) -> bool:
        if response.status not in wanted:
            return False
        body = _json_object(response.text) or {}
        if any(k not in body for k in keys):
            return False
        lowered = response.text.lower()
        return all(f.lower() in lowered for f in body_contains)

    return accept


@dataclass(frozen=True)
class WriteProbe:
    verdict: WriteVerdict
    refusal: ApiResponse
    control: ApiResponse
    positive: ApiResponse

    @property
    def passed(self) -> bool:
        return self.verdict is WriteVerdict.REACHES_SERVICE

    def describe(self) -> str:
        return (
            f"{self.verdict.value}: probe {self.refusal.status} {self.refusal.text[:200]!r}; "
            f"control {self.control.status}; positive {self.positive.status}"
        )

    def require(self) -> WriteProbe:
        if not self.passed:
            raise AssertionError(f"write route probe failed: {self.describe()}")
        return self


class _Engine(Protocol):
    """``HttpEngine`` or a fake with the same ``request``."""

    def request(self, method: str, path: str, **kw: object) -> ApiResponse: ...


def probe_write(
    engine: _Engine,
    method: str,
    path: str,
    body: object,
    *,
    control: tuple[str, str],
    positive: str,
    accept: Accept,
    shapes: NotFoundShapes = DEFAULT_SHAPES,
    read_only: bool | None = None,
) -> WriteProbe:
    """Prove ``method path`` reaches its service without changing anything.

    ``control`` is ``(method, path)`` outside the route predicate; ``positive``
    a GET path the route serves. Every answer must be measured (not a 401 or a
    firewall's plain-text 403). Skips on a read-only environment.
    """
    from gate.read_only import require_writable

    require_writable(f"write route probe {method.upper()} {path}", read_only=read_only)
    probe = require_measured(engine.request(method.upper(), path, json=body))
    control_method, control_path = control
    negative = require_measured(engine.request(control_method.upper(), control_path, json=body))
    known = require_measured(engine.request("GET", positive))

    def result(verdict: WriteVerdict) -> WriteProbe:
        return WriteProbe(verdict, probe, negative, known)

    if not known.is_success:
        return result(WriteVerdict.POSITIVE_FAILED)
    if not is_gateway_no_route(negative, shapes):
        return result(WriteVerdict.CONTROL_NOT_GATEWAY)
    if probe.is_success:
        return result(WriteVerdict.SIDE_EFFECT)
    if is_gateway_no_route(probe, shapes):
        return result(WriteVerdict.NOT_ROUTED)
    if not accept(probe):
        return result(WriteVerdict.UNEXPECTED_REFUSAL)
    return result(WriteVerdict.REACHES_SERVICE)


@dataclass(frozen=True)
class HeadProbe:
    created_id: str
    while_present: int
    after_discard: int

    @property
    def passed(self) -> bool:
        return self.while_present == 200 and self.after_discard == 404

    def require(self) -> HeadProbe:
        if not self.passed:
            raise AssertionError(
                f"HEAD probe on {self.created_id}: {self.while_present} while present "
                f"(expected 200), {self.after_discard} after discard (expected 404)"
            )
        return self


def probe_head(
    engine: _Engine,
    path_for: Callable[[str], str],
    create: Callable[[], str],
    discard: Callable[[str], object],
    *,
    read_only: bool | None = None,
) -> HeadProbe:
    """HEAD a resource the test created (200), discard it, HEAD again (404).

    ``discard`` runs even when the first HEAD raises, so the probe leaves
    nothing behind.
    """
    from gate.read_only import require_writable

    require_writable("HEAD route probe (creates a resource)", read_only=read_only)
    created = create()
    try:
        present = require_measured(engine.request("HEAD", path_for(created))).status
    finally:
        discard(created)
    gone = require_measured(engine.request("HEAD", path_for(created))).status
    return HeadProbe(created, present, gone)
