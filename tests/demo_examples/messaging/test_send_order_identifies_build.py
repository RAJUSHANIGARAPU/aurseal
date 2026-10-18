"""The observed send order identifies which ordering (build) the sender runs."""

from __future__ import annotations

import pytest

from oracles.send_order import (
    BY_TYPE,
    TYPE_THEN_ID,
    TYPE_THEN_NAME,
    files_from,
    fitting_hypotheses,
    identify,
)
from tests.demo_examples.messaging import support

BUILDS = {"v1": TYPE_THEN_ID, "v2": TYPE_THEN_NAME}


@pytest.mark.parametrize("order", ["v1", "v2"])
def test_send_order_identifies_build(demo_sut, demo_seed, outbound, frozen, order):
    frozen.settings(send_order=order)
    sub = support.booked(demo_seed)
    (message,) = outbound.overview(sub["id"])
    outbound.generate(sub["id"])
    for name in ("b-notes.txt", "a-notes.txt"):  # ids ascend b, a; names ascend a, b
        demo_sut.client.ok("POST", f"/api/messages/{message['id']}/attachments", {"name": name})

    # the system's own sender delivers; the inspection client cannot
    demo_sut.client.ok("POST", "/api/messages/send", {"subscription_id": sub["id"]})
    files = files_from(outbound.details(message["id"]))
    assert len(files) == 4 and all(f.sent for f in files)
    assert BY_TYPE.name in fitting_hypotheses(files, [BY_TYPE])
    assert identify(files, BUILDS) == order, fitting_hypotheses(files, BUILDS.values())


def test_without_a_tie_the_message_does_not_identify_the_build(demo_sut, demo_seed, outbound):
    sub = support.booked(demo_seed)
    (message,) = outbound.overview(sub["id"])
    outbound.generate(sub["id"])
    demo_sut.client.ok("POST", "/api/messages/send", {"subscription_id": sub["id"]})
    files = files_from(outbound.details(message["id"]))
    assert identify(files, BUILDS) is None  # one file per type: every ordering fits
