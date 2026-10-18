"""Customer screening step: the button is found by any known label, then the gate opens."""

from __future__ import annotations

import pytest

from tests.demo_examples.lifecycle import support
from tests.demo_examples.lifecycle.screens import element_texts, match_label_variant

SCREENING_LABELS = ("Run screening", "Start check")


@pytest.mark.parametrize("label", SCREENING_LABELS)
def test_screening_step_survives_a_renamed_button(demo_sut, demo_http, demo_seed, frozen, label):
    frozen.settings(screening_label=label)
    screen = demo_sut.client.get("/ui/subscriptions/new")
    assert screen.status == 200
    (button,) = element_texts(screen.body.decode(), "screening-run")
    assert match_label_variant(button, SCREENING_LABELS) == label

    customer = demo_seed.customer(verified=False)
    assert customer["status"] == "pending_verification"
    screened = support.ok(demo_http.post(f"customers/{customer['id']}/actions/verify"))
    assert screened["status"] == "verified" and screened["verified_at"]
    sub = demo_seed.subscription(customer=screened, state="draft")
    assert sub["holder"]["id"] == customer["id"]


def test_an_unknown_label_fails_loudly():
    with pytest.raises(LookupError, match="none of the accepted variants"):
        match_label_variant("Check now", SCREENING_LABELS)
    assert element_texts('<p data-testid="x">a <b>b</b></p><br>', "x") == ["a b"]
