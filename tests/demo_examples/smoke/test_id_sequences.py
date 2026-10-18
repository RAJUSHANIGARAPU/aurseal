"""Numbers issued by the service: well formed, strictly increasing per type, never client-chosen."""

from __future__ import annotations

import re

from demo_sut.seeding import default_address
from oracles.id_sequences import Issued, check_sequence

#: ``TYPE-UNIT-n``: the demo issues CUS-D1-n and PAR-D1-n with an unpadded counter.
PATTERN = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<segment>[A-Z0-9]+)-(?P<counter>\d+)$")


def test_issued_numbers_well_formed_monotonic(demo_seed, demo_sut, demo_control):
    client = demo_sut.client
    # control: a number a client sends is ignored; the seeding fixtures send none at all
    body = {
        "type": "INDIVIDUAL",
        "display_name": "Client Sent Number",
        "billing_address": default_address(),
        "number": "CUS-D1-999999",
    }
    sent = client.ok("POST", "/api/customers", body)
    assert sent["number"] != "CUS-D1-999999", "the service kept a client-chosen number"

    customers = [sent] + [demo_seed.customer(verified=False) for _ in range(2)]
    customers.append(demo_seed.customer(type="COMPANY", display_name="Sequence Co", verified=False))
    partners = [demo_seed.partner() for _ in range(2)]

    for prefix, created in (("CUS", customers), ("PAR", partners)):
        issued = [Issued(str(e["id"]), [e["number"]]) for e in created]
        report = check_sequence(issued, expected_prefix=prefix, pattern=PATTERN, padded_length=1)
        assert report.ok, f"{prefix}: {report.problems()}"
        # read back: the number is kept on the stored record
        path = "/api/customers" if prefix == "CUS" else "/api/partners"
        for entity in created:
            assert client.ok("GET", f"{path}/{entity['id']}")["number"] == entity["number"]
    units = {n["number"].split("-")[1] for n in customers + partners}
    assert units == {"D1"}, f"numbers span several units: {units}"
