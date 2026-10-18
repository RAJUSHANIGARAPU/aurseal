"""Customer creation probes: one parametrised fixture, each probe a different set of values.

``customer-matrix.json`` creates a customer, verifies them and subscribes them with a
reseller share. Its placeholders are ``DISPLAY_NAME``, ``EMAIL``, ``ACCOUNT``, ``COUNTRY``,
``POSTAL_CODE`` and ``SHARE``. A probe's observation is where (and whether) the journey
was refused, and with which message; :func:`probes.run_probe` records it instead of
failing on the first refusal.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from demo_sut.customers import HOME_COUNTRY, NAME_LIMIT, make_iban
from test_data import fixture_contract as contract
from test_data.fixture_replay import unbound_placeholders
from tests.demo_examples.lifecycle import support
from tests.demo_examples.lifecycle.probes import run_probe

FIXTURE = "customer-matrix"
SHARE_CAP = 20
GOOD_ACCOUNT = make_iban(bban="PROBE000000001")
_tags = itertools.count(1)
BROKEN_ACCOUNT = GOOD_ACCOUNT[:2] + f"{(int(GOOD_ACCOUNT[2:4]) + 1) % 100:02d}" + GOOD_ACCOUNT[4:]


def name_of_length(tag: str, length: int) -> str:
    head = f"Probe {tag} "
    return head + "N" * (length - len(head))


def base(tag: str) -> dict[str, Any]:
    return {
        "DISPLAY_NAME": f"Probe Member {tag}",
        "EMAIL": "probe.member@example.test",
        "ACCOUNT": GOOD_ACCOUNT,
        "COUNTRY": HOME_COUNTRY,
        "POSTAL_CODE": "ZT 2290",
        "SHARE": 5,
    }


#: probe -> (overrides given the run tag, refused at step, message)
PROBES = {
    "baseline": (lambda tag: {}, None, None),
    "name-at-limit": (lambda tag: {"DISPLAY_NAME": name_of_length(tag, NAME_LIMIT)}, None, None),
    "name-over-limit": (
        lambda tag: {"DISPLAY_NAME": name_of_length(tag, NAME_LIMIT + 1)},
        "create",
        "Display name may be at most 64 characters.",
    ),
    "non-ascii-name": (lambda tag: {"DISPLAY_NAME": f"Zoë Ångström {tag}"}, None, None),
    "email-missing-for-email-delivery": (
        lambda tag: {"EMAIL": ""},
        "subscribe",
        "Invoices by e-mail need an e-mail address.",
    ),
    "account-checksum": (
        lambda tag: {"ACCOUNT": BROKEN_ACCOUNT},
        "create",
        "Account number fails its check digits.",
    ),
    "foreign-postal-code": (lambda tag: {"COUNTRY": "AA", "POSTAL_CODE": "12345"}, None, None),
    "share-at-cap": (lambda tag: {"SHARE": SHARE_CAP}, None, None),
    "share-over-cap": (
        lambda tag: {"SHARE": SHARE_CAP + 1},
        "subscribe",
        "Reseller share may not exceed the cap.",
    ),
}


def test_the_matrix_fixture_is_sound_offline():
    fixture = support.load_fixture(FIXTURE)
    params = set(base("t")) | {"partner_id", "plan_id"}
    assert unbound_placeholders(fixture, params) == set()
    doc = json.loads((support.FIXTURES / f"{FIXTURE}.json").read_text(encoding="utf-8"))
    rules = [
        contract.Required("create.body.type"),
        contract.Required("create.body.display_name"),
        contract.Required("create.body.billing_address.line1"),
        contract.Required("create.body.billing_address.city"),
    ]
    assert contract.check({FIXTURE: doc}, rules) == []
    assert [s.name for s in fixture.steps] == ["verify", "subscribe"]
    assert BROKEN_ACCOUNT != GOOD_ACCOUNT and len(BROKEN_ACCOUNT) == len(GOOD_ACCOUNT)


@pytest.mark.parametrize("probe", sorted(PROBES))
def test_customer_creation_probe(probe, demo_http, demo_seed, demo_control):
    overrides, refused_at, message = PROBES[probe]
    tag = f"T{next(_tags):03d}"
    values = {**base(tag), **overrides(tag), "partner_id": 1, "plan_id": demo_seed.plan_id()}
    result = run_probe(demo_http, support.load_fixture(FIXTURE), values, today=support.TODAY)

    if refused_at is None:
        assert result.accepted, result.describe()
        assert result.values["customer_name"] == values["DISPLAY_NAME"], "the name changed"
        assert result.values["share"] == values["SHARE"]
    else:
        assert result.refused_at == refused_at, result.describe()
        assert result.signals.api_status == 422, result.describe()
        assert result.message == message
