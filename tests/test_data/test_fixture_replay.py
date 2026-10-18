"""Tests for test_data.fixture_replay and test_data.factory against an offline fake service."""

from __future__ import annotations

import json
from datetime import date

import pytest

from api.clients.http_engine import HttpEngine
from test_data.factory import Prerequisite, PrerequisiteFactory
from test_data.fixture_replay import (
    Fixture,
    FixtureSeeder,
    SeedError,
    bind,
    run_day_values,
    unbound_placeholders,
)
from tests.api_layer.fakes import FakeAdapter, make_response, session_with


class FakeOrders:
    """A tiny versioned service: create, versioned updates, a step that is not always offered."""

    def __init__(self) -> None:
        self.version = 0
        self.bodies: list[dict] = []
        self.adapter = (
            FakeAdapter()
            .on("POST", "/orders/o-1/confirm", lambda r: make_response(409, {"error": "n/a"}))
            .on("POST", "/orders/o-1/broken", lambda r: make_response(500, {"error": "boom"}))
            .on("POST", "/orders/o-1/rewind", lambda r: make_response(200, {"version": 0}))
            .on("POST", "/orders", self._create)
            .on("PUT", "/orders/o-1", self._update)
            .on("GET", "/orders/o-1", lambda r: make_response(200, {"total": {"net": 12.5}}))
        )

    def _create(self, request):
        self.bodies.append(json.loads(request.body))
        self.version = 1
        return make_response(201, {"id": "o-1", "version": 1}, {"Location": "/orders/o-1"})

    def _update(self, request):
        assert f"version={self.version}" in request.url, "the current version must be threaded"
        self.bodies.append(json.loads(request.body))
        self.version += 2  # versions are not contiguous
        return make_response(200, {"version": self.version, "line": {"id": f"l-{self.version}"}})

    def seeder(self) -> FixtureSeeder:
        engine = HttpEngine("https://svc.example/", session=session_with(self.adapter))
        return FixtureSeeder(engine, version_field="version", today=date(2030, 3, 15))


FIXTURE = {
    "name": "order",
    "create": {
        "method": "POST",
        "path": "/orders",
        "body": {"customer": "${customer}", "placed": "${TODAY_TOKEN}", "qty": "${qty}"},
        "capture": {"order_id": "id", "where": "@location"},
    },
    "steps": [
        {
            "method": "PUT",
            "path": "/orders/${order_id}?version=${version}",
            "body": {"due": "${RUN_DAY_DATE_PLUS_30D}", "ref": "ord-${order_id}"},
            "capture": {"line_id": "line.id"},
        },
        {"method": "POST", "path": "/orders/${order_id}/confirm", "optional": True},
        {"method": "PUT", "path": "/orders/${order_id}?version=${version}", "body": {}},
    ],
}


class TestBind:
    def test_whole_placeholder_keeps_type_and_partial_is_interpolated(self):
        out = bind({"a": "${n}", "b": "x-${n}", "c": ["${n}"]}, {"n": 3}, lambda _: None)
        assert out == {"a": 3, "b": "x-3", "c": [3]}

    def test_unbound_placeholder_fails(self):
        with pytest.raises(SeedError, match="nope"):
            bind("${nope}", {}, lambda _: None)

    def test_run_day_offsets(self):
        resolve = run_day_values(date(2030, 1, 31))
        assert resolve("TODAY_TOKEN") == "2030-01-31"
        assert resolve("RUN_DAY_DATE_PLUS_1D") == "2030-02-01"
        assert resolve("RUN_DAY_DATE_MINUS_31D") == "2029-12-31"
        assert resolve("OTHER") is None


class TestReplay:
    def test_full_replay_threads_versions_captures_and_skips_optional(self):
        svc = FakeOrders()
        result = svc.seeder().replay(Fixture.from_dict(FIXTURE), {"customer": "c-9", "qty": 2})
        assert svc.bodies[0] == {"customer": "c-9", "placed": "2030-03-15", "qty": 2}
        assert svc.bodies[1] == {"due": "2030-04-14", "ref": "ord-o-1"}
        assert result["order_id"] == "o-1" and result["where"] == "o-1"
        assert result["line_id"] == "l-3" and result.version == 5
        assert result.skipped == ["step 2"]

    def test_required_step_failure_names_the_step_and_quotes_the_response(self):
        fx = Fixture.from_dict(
            {"name": "f", "steps": [{"method": "POST", "path": "/orders/o-1/broken"}]}
        )
        with pytest.raises(SeedError, match=r"f: step 1 POST failed: HTTP 500.*boom"):
            FakeOrders().seeder().replay(fx)

    def test_version_going_backwards_fails(self):
        fx = Fixture.from_dict({"steps": [{"method": "POST", "path": "/orders/o-1/rewind"}]}, "rw")
        with pytest.raises(SeedError, match="backwards"):
            FakeOrders().seeder().replay(fx, version=4)

    def test_missing_capture_fails(self):
        raw = {
            "steps": [
                {
                    "method": "PUT",
                    "path": "/orders/o-1?version=0",
                    "body": {},
                    "capture": {"x": "no.such"},
                }
            ]
        }
        with pytest.raises(SeedError, match="no 'no.such'"):
            FakeOrders().seeder().replay(Fixture.from_dict(raw, "c"), version=0)

    def test_read_back_helpers(self):
        seeder = FakeOrders().seeder()
        assert seeder.read_field("/orders/${id}", "total.net", {"id": "o-1"}) == 12.5
        with pytest.raises(SeedError, match="read-back"):
            seeder.read("/missing")

    def test_malformed_step_and_missing_file_fail_loudly(self, tmp_path):
        with pytest.raises(SeedError, match="missing"):
            Fixture.from_dict({"steps": [{"method": "PUT"}]})
        with pytest.raises(SeedError, match="cannot load"):
            Fixture.load(tmp_path / "nope.json")

    def test_load_from_file(self, tmp_path):
        p = tmp_path / "order.json"
        p.write_text(json.dumps({"steps": [{"method": "GET", "path": "/x"}]}))
        assert Fixture.load(p).name == "order"


def test_unbound_placeholders_is_an_offline_contract_check():
    fx = Fixture.from_dict(FIXTURE)
    assert unbound_placeholders(fx, {"customer", "qty"}) == set()
    assert unbound_placeholders(fx, {"customer"}) == {"qty"}


class TestFactory:
    def _fixture(self, name: str, body: dict) -> Fixture:
        return Fixture.from_dict(
            {
                "create": {
                    "method": "POST",
                    "path": f"/{name}",
                    "body": body,
                    "capture": {"id": "id"},
                }
            },
            name,
        )

    def _engine(self, log: list):
        counter = iter(range(1, 100))

        def create(request):
            body = json.loads(request.body)
            log.append((request.url.rsplit("/", 1)[-1], body))
            return make_response(201, {"id": f"id-{next(counter)}"})

        adapter = FakeAdapter().on("POST", "/", create)
        return FixtureSeeder(HttpEngine("https://svc.example/", session=session_with(adapter)))

    def test_provisions_in_dependency_order_once_with_a_run_tag(self):
        log: list = []
        factory = PrerequisiteFactory(
            self._engine(log),
            [
                Prerequisite(
                    "order",
                    self._fixture("orders", {"c": "${customer.id}", "p": "${pricelist.id}"}),
                    depends_on=("customer", "pricelist"),
                ),
                Prerequisite(
                    "customer",
                    self._fixture(
                        "customers", {"name": "Cust ${SEED_TAG}", "p": "${pricelist.id}"}
                    ),
                    depends_on=("pricelist",),
                ),
                Prerequisite("pricelist", self._fixture("pricelists", {"name": "PL ${SEED_TAG}"})),
            ],
            seed_tag="t3st",
        )
        built = factory.provision("order")
        assert [name for name, _ in log] == ["pricelists", "customers", "orders"]
        assert log[1][1] == {"name": "Cust t3st", "p": "id-1"}
        assert log[2][1] == {"c": "id-2", "p": "id-1"}
        assert built["order"]["id"] == "id-3"
        factory.provision("customer")
        assert len(log) == 3, "already provisioned prerequisites are reused"

    def test_cycle_and_unknown_names_fail(self):
        fx = self._fixture("x", {})
        factory = PrerequisiteFactory(
            self._engine([]),
            [Prerequisite("a", fx, depends_on=("b",)), Prerequisite("b", fx, depends_on=("a",))],
        )
        with pytest.raises(ValueError, match="cycle"):
            factory.order("a")
        with pytest.raises(KeyError):
            factory.order("zzz")
        with pytest.raises(ValueError, match="unique"):
            PrerequisiteFactory(self._engine([]), [Prerequisite("a", fx), Prerequisite("a", fx)])
