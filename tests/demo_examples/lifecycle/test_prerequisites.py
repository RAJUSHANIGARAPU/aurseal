"""The prerequisite factory provisions a dependency chain over REST, in order, run-tagged."""

from __future__ import annotations

from test_data.factory import Prerequisite, PrerequisiteFactory
from tests.demo_examples.lifecycle import support


def test_factory_provisions_prerequisite_chain(demo_http, replay):
    factory = PrerequisiteFactory(
        replay,
        [
            Prerequisite(
                "reseller", support.load_fixture("reseller"), depends_on=("reseller_terms",)
            ),
            Prerequisite(
                "reseller_terms",
                support.load_fixture("reseller-terms"),
                params={"share_percent": 4.5},
            ),
            Prerequisite("plan", support.load_fixture("plan")),
            Prerequisite(
                "customer",
                support.load_fixture("customer-standard"),
                params={"payout_account": support.new_account_number()},
            ),
        ],
    )
    assert factory.order("reseller") == ["reseller_terms", "reseller"]
    built = factory.provision("reseller", "plan", "customer")
    terms = factory.provision("reseller_terms")["reseller_terms"]

    assert all(r["id"] is not None for r in [*built.values(), terms])
    assert built["reseller"]["number"].strip()
    tag = factory.seed_tag
    assert tag in terms["name"] and tag in built["reseller"]["name"]
    assert built["plan"]["code"] == f"PLAN-{tag}"

    reseller = support.ok(demo_http.get(f"partners/{built['reseller']['id']}"))
    assert reseller["reseller_terms_id"] == terms["id"]
    support.ok(demo_http.post(f"customers/{built['customer']['id']}/actions/verify"))
    sub = support.ok(
        demo_http.post(
            "subscriptions",
            json={
                "customer_id": built["customer"]["id"],
                "partner_id": built["reseller"]["id"],
                "plan_id": built["plan"]["id"],
            },
        )
    )
    assert sub["commission_percentage"] == 4.5  # the chain is wired end to end
