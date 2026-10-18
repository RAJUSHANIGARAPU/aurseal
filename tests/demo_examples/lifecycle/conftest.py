"""Seeding fixtures for the lifecycle examples.

Everything is built over the API by replaying the JSON fixtures in
``tests/demo_examples/fixtures`` with :class:`test_data.fixture_replay.FixtureSeeder`,
with live ids resolved by :class:`test_data.parties.LiveResolver` and prerequisites
provisioned by :class:`test_data.factory.PrerequisiteFactory`. The demo clock is frozen
at :data:`support.NOW` and the seeder's run day is the same date, so ``${TODAY_TOKEN}``
offsets mean the same day on both sides.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from api.clients.http_engine import HttpEngine
from test_data.cleanup import CleanupLedger
from test_data.factory import Prerequisite, PrerequisiteFactory
from test_data.fixture_replay import FixtureSeeder, SeededEntity
from test_data.parties import LiveResolver
from tests.demo_examples.lifecycle import support

#: The reseller and plan the seeds bind to, resolved live by name / code.
PARTNER_NAME = "Seed Channel Partner"
PLAN_CODES = {"TEAM": "TEAM-STD", "INDIVIDUAL": "SOLO-STD"}


def verify_customer(api: HttpEngine) -> Callable[[SeededEntity], None]:
    """Prerequisite after-hook: complete the customer verification gate."""

    def after(result: SeededEntity) -> None:
        verified = support.ok(api.post(f"customers/{result['id']}/actions/verify"))
        result.values["status"] = verified["status"]

    return after


@pytest.fixture
def frozen(demo_control: Any) -> Any:
    demo_control.freeze(support.NOW)
    return demo_control


@pytest.fixture
def replay(demo_http: HttpEngine, frozen: Any) -> FixtureSeeder:
    return FixtureSeeder(demo_http, today=support.TODAY)


@pytest.fixture
def live(demo_http: HttpEngine) -> LiveResolver:
    return LiveResolver(
        lambda kind: support.ok(demo_http.get(kind)),
        name_of=lambda r: str(r.get("code") or r.get("name") or ""),
    )


@pytest.fixture
def prerequisites(replay: FixtureSeeder, demo_http: HttpEngine) -> PrerequisiteFactory:
    customer = Prerequisite(
        "customer",
        support.load_fixture("customer-standard"),
        params={"payout_account": support.new_account_number()},
        after=verify_customer(demo_http),
    )
    return PrerequisiteFactory(replay, [customer])


@pytest.fixture
def verified_customer(prerequisites: PrerequisiteFactory) -> SeededEntity:
    return prerequisites.provision("customer")["customer"]


@pytest.fixture
def cleanup(demo_http: HttpEngine) -> Iterator[CleanupLedger]:
    ledger = CleanupLedger()
    yield ledger
    report = ledger.discard_all()
    assert report.clean, f"cleanup failed: {report.failed}"


def _binding(live: LiveResolver, customer: SeededEntity, plan_kind: str) -> dict[str, Any]:
    return {
        "customer_id": customer["id"],
        "partner_id": live.resolve("partners", PARTNER_NAME)["id"],
        "plan_id": live.resolve("plans", PLAN_CODES[plan_kind])["id"],
    }


@pytest.fixture
def seeded_pending_subscription(
    replay: FixtureSeeder, live: LiveResolver, verified_customer: SeededEntity
) -> SeededEntity:
    """A subscription driven over the whole quote journey to ``pending_payment``."""
    params = {
        **_binding(live, verified_customer, "TEAM"),
        "frequency": "MONTHLY",
        "debit_account": support.new_account_number(),
    }
    return replay.replay(support.load_fixture("subscription-to-pending-payment"), params)


@pytest.fixture
def seed_active_subscription(
    replay: FixtureSeeder,
    live: LiveResolver,
    verified_customer: SeededEntity,
    demo_http: HttpEngine,
) -> Callable[..., dict[str, Any]]:
    """``seed(plan_kind="TEAM", frequency="MONTHLY", start_date=today)`` -> an active one."""

    def seed(
        plan_kind: str = "TEAM", frequency: str = "MONTHLY", start_date: str | None = None
    ) -> dict[str, Any]:
        params = {
            **_binding(live, verified_customer, plan_kind),
            "frequency": frequency,
            "start_date": start_date or support.TODAY.isoformat(),
        }
        result = replay.replay(support.load_fixture("subscription-to-active"), params)
        return support.subscription(demo_http, result["subscription_id"])

    return seed


@pytest.fixture
def seed_billed_subscription(
    seed_active_subscription: Callable[..., dict[str, Any]], demo_http: HttpEngine
) -> Callable[..., dict[str, Any]]:
    """Like ``seed_active_subscription`` and its first invoice is billed (period booked)."""

    def seed(**kwargs: Any) -> dict[str, Any]:
        sub = seed_active_subscription(**kwargs)
        first = min(support.transactions(demo_http, sub["id"]), key=lambda t: t["period_start"])
        support.book_due(demo_http, sub["id"], until=first["period_start"])
        return support.subscription(demo_http, sub["id"])

    return seed


@pytest.fixture
def seed_renewable_subscription(
    replay: FixtureSeeder, live: LiveResolver, verified_customer: SeededEntity
) -> Callable[..., SeededEntity]:
    """Backdated one year and billed up to the renewal date, so a renewal is due today."""

    def seed(frequency: str = "YEARLY", plan_kind: str = "TEAM") -> SeededEntity:
        params = {**_binding(live, verified_customer, plan_kind), "frequency": frequency}
        return replay.replay(support.load_fixture("subscription-backdated-renewable"), params)

    return seed


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Detector examples here switch their own faults on: keep their verdicts out of the run's
    evidence file, one file per test."""
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)
    return path
