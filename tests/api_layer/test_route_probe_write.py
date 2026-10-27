"""Tests for the side-effect-free write-route and HEAD probes in api.clients.route_probe."""

from __future__ import annotations

import json

import pytest

from api.clients.http_engine import ApiResponse
from api.clients.route_probe import (
    UnmeasuredResponse,
    WriteVerdict,
    probe_head,
    probe_write,
    service_refusal,
)

GATEWAY_404 = {"path": "/x", "status": 404, "error": "Not Found", "requestId": "r"}
PROBLEM = {"title": "Bad Request", "status": 422, "detail": "plan_code is required"}


def _r(status: int, body: object = b"") -> ApiResponse:
    return ApiResponse(status, body if isinstance(body, bytes) else json.dumps(body).encode())


class FakeGateway:
    """Answers by (method, path); records every request it saw."""

    def __init__(self, answers: dict[tuple[str, str], ApiResponse]):
        self.answers = answers
        self.seen: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **kw: object) -> ApiResponse:
        self.seen.append((method, path))
        return self.answers.get((method, path), _r(404, GATEWAY_404))


ACCEPT = service_refusal({400, 422}, keys=("title",), body_contains=("plan_code",))
ROUTED = {
    ("POST", "/plans/00000000-dead"): _r(422, PROBLEM),
    ("GET", "/plans"): _r(200, []),
}


def _probe(answers, **kw):
    engine = FakeGateway(answers)
    result = probe_write(
        engine,
        "post",
        "/plans/00000000-dead",
        {"plan_code": ""},
        control=("PATCH", "/plansx/00000000-dead"),
        positive="/plans",
        accept=ACCEPT,
        read_only=False,
        **kw,
    )
    return result, engine


def test_service_refusal_with_both_controls_proves_the_route():
    result, engine = _probe(ROUTED)
    assert result.verdict is WriteVerdict.REACHES_SERVICE and result.passed
    assert result.require() is result
    assert engine.seen[0] == ("POST", "/plans/00000000-dead")


@pytest.mark.parametrize(
    ("answers", "verdict"),
    [
        ({**ROUTED, ("POST", "/plans/00000000-dead"): _r(404, GATEWAY_404)}, "NOT_ROUTED"),
        ({**ROUTED, ("POST", "/plans/00000000-dead"): _r(201, {"id": 1})}, "SIDE_EFFECT"),
        ({**ROUTED, ("POST", "/plans/00000000-dead"): _r(500, PROBLEM)}, "UNEXPECTED_REFUSAL"),
        ({**ROUTED, ("POST", "/plans/00000000-dead"): _r(422, {"x": 1})}, "UNEXPECTED_REFUSAL"),
        ({**ROUTED, ("PATCH", "/plansx/00000000-dead"): _r(405, PROBLEM)}, "CONTROL_NOT_GATEWAY"),
        ({**ROUTED, ("GET", "/plans"): _r(502, b"{}")}, "POSITIVE_FAILED"),
    ],
)
def test_every_other_outcome_is_named_and_not_a_pass(answers, verdict):
    result, _ = _probe(answers)
    assert result.verdict is WriteVerdict[verdict] and not result.passed
    with pytest.raises(AssertionError, match=WriteVerdict[verdict].value):
        result.require()


def test_an_unauthenticated_answer_is_unmeasured():
    with pytest.raises(UnmeasuredResponse):
        _probe({**ROUTED, ("POST", "/plans/00000000-dead"): _r(401)})


def test_write_probes_skip_on_a_read_only_environment():
    engine = FakeGateway(ROUTED)
    with pytest.raises(pytest.skip.Exception, match="read-only"):
        probe_write(
            engine,
            "POST",
            "/p",
            {},
            control=("GET", "/q"),
            positive="/plans",
            accept=ACCEPT,
            read_only=True,
        )
    assert engine.seen == []


class FakeResource:
    def __init__(self, *, head_after_discard: int = 404, head_present: int = 200):
        self.present: set[str] = set()
        self.after = head_after_discard
        self.head_present = head_present
        self.discarded: list[str] = []

    def create(self) -> str:
        self.present.add("r-1")
        return "r-1"

    def discard(self, ident: str) -> None:
        self.present.discard(ident)
        self.discarded.append(ident)

    def request(self, method: str, path: str, **kw: object) -> ApiResponse:
        assert method == "HEAD"
        ident = path.rsplit("/", 1)[-1]
        return ApiResponse(self.head_present if ident in self.present else self.after)


def test_head_is_200_while_present_and_404_after_discard():
    fake = FakeResource()
    result = probe_head(fake, lambda i: f"/files/{i}", fake.create, fake.discard, read_only=False)
    assert result.passed and result.require() is result and fake.discarded == ["r-1"]


def test_head_that_still_answers_after_discard_fails():
    fake = FakeResource(head_after_discard=200)
    result = probe_head(fake, lambda i: f"/files/{i}", fake.create, fake.discard, read_only=False)
    with pytest.raises(AssertionError, match="200 after discard"):
        result.require()


def test_head_discards_even_when_the_first_probe_raises():
    fake = FakeResource(head_present=401)
    with pytest.raises(UnmeasuredResponse):
        probe_head(fake, lambda i: f"/files/{i}", fake.create, fake.discard, read_only=False)
    assert fake.discarded == ["r-1"]


def test_head_skips_before_creating_on_a_read_only_environment():
    fake = FakeResource()
    with pytest.raises(pytest.skip.Exception):
        probe_head(fake, lambda i: i, fake.create, fake.discard, read_only=True)
    assert fake.present == set()


def test_env_read_only_policy():
    from gate.read_only import env_read_only

    class Config:
        def __init__(self, value):
            self.value = value

        def get(self, key):
            return self.value

    assert env_read_only(Config(True)) is True
    assert env_read_only(Config("off")) is False
    with pytest.raises(ValueError):
        env_read_only(Config("maybe"))
