"""Create-to-state seeds (active, billed) and how they stock the seed modes' pool."""

from __future__ import annotations

import pytest

from test_data.pool import NoTarget, ReusePool, replenish, select_targets
from test_data.seed_mode import resolve
from tests.demo_examples.lifecycle import support


def first_period_booked(api, sub) -> bool:
    txs = support.transactions(api, sub["id"])
    return bool(txs) and min(txs, key=lambda t: t["period_start"])["state"] == "BOOKED"


def billed_pool(api) -> ReusePool:
    return ReusePool(
        lambda: support.ok(api.get("subscriptions", params={"status": "active"})),
        fit=lambda s: first_period_booked(api, s),
        label=lambda s: s["number"],
        preference=lambda s: -s["id"],
    )


def test_seed_active_and_seed_billed_reach_their_states(
    demo_http, seed_active_subscription, seed_billed_subscription
):
    active = seed_active_subscription()
    assert active["status"] == "active" and not first_period_booked(demo_http, active)
    assert support.ok(demo_http.get(f"subscriptions/{active['id']}/invoices")) == []

    billed = seed_billed_subscription(plan_kind="INDIVIDUAL")
    assert billed["status"] == "active" and first_period_booked(demo_http, billed)
    (invoice,) = support.ok(demo_http.get(f"subscriptions/{billed['id']}/invoices"))
    assert invoice["kind"] == "NEW"


@pytest.mark.parametrize("mode", ["fresh", "reuse", "targets"])
def test_seed_modes_take_targets_from_the_replenished_pool(
    demo_http, seed_active_subscription, seed_billed_subscription, tmp_path, mode
):
    def seed(booked: bool):
        return seed_billed_subscription() if booked else seed_active_subscription()

    stocked = replenish(
        2, seed, booked=True, label=lambda s: s["number"], path=tmp_path / "targets.txt"
    )
    assert len(stocked.seeded) == 2 and not stocked.failures
    env = {"SEED_MODE": mode}
    if mode != "fresh":
        env["ENTITY_ALLOWLIST_FILE"] = str(stocked.path)

    targets = select_targets(resolve(env), billed_pool(demo_http), lambda: seed(True), limit=2)

    if mode == "fresh":
        assert [t.was_seeded for t in targets] == [True]
        assert targets[0].label not in stocked.seeded
    else:
        assert sorted(t.label for t in targets) == sorted(stocked.seeded)
        assert not any(t.was_seeded for t in targets)


def test_targets_mode_never_builds_a_missing_shape(demo_http, seed_active_subscription):
    unbilled = seed_active_subscription()
    config = resolve({"SEED_MODE": "targets", "ENTITY_ALLOWLIST": unbilled["number"]})
    with pytest.raises(NoTarget, match="targets mode never builds one"):
        select_targets(config, billed_pool(demo_http), seed_active_subscription)
