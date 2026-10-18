"""Fields fixed for the term cannot be edited in a change: the refusal names the field."""

from __future__ import annotations

import pytest

from tests.demo_examples.change.support import active_subscription, current

LOCKED = {"currency": "USD", "plan_id": 2, "frequency": "QUARTERLY"}


def attempt(seed, sub_id: int, field: str, value, *, draft_id: int | None = None):
    body = {"changes": {field: value}}
    if draft_id is None:
        return seed.client.post(f"/api/subscriptions/{sub_id}/drafts", {"kind": "change", **body})
    return seed.client.patch(f"/api/drafts/{draft_id}", body)


def test_currency_change_is_refused(demo_seed, demo_control):
    sub = active_subscription(demo_seed, demo_control, now="2026-07-14", start="2026-06-01")
    assert sub["currency"] == "EUR", "control: the currency is readable before the attempt"
    response = attempt(demo_seed, sub["id"], "currency", "USD")
    assert response.status == 422
    assert response.json()["fields"] == {"currency": "currency cannot be edited in a change."}
    after = current(demo_seed, sub["id"])
    assert after["currency"] == "EUR" and after["status"] == "active" and not after["open_draft_id"]


@pytest.mark.parametrize("field", sorted(LOCKED))
@pytest.mark.parametrize("route", ["open", "autosave"])
def test_every_locked_field_is_refused_on_open_and_on_autosave(
    demo_seed, demo_control, field, route
):
    sub = active_subscription(demo_seed, demo_control, now="2026-07-14", start="2026-06-01")
    draft_id = None
    if route == "autosave":
        draft_id = demo_seed.draft(sub["id"], "change")["id"]
    response = attempt(demo_seed, sub["id"], field, LOCKED[field], draft_id=draft_id)
    assert response.status == 422, response.body[:200]
    assert response.json()["fields"] == {field: f"{field} cannot be edited in a change."}
    assert current(demo_seed, sub["id"])[field] == sub[field]


def test_the_change_screen_shows_the_locked_fields_read_only(demo_seed, demo_control):
    sub = active_subscription(demo_seed, demo_control, now="2026-07-14", start="2026-06-01")
    draft = demo_seed.draft(sub["id"], "change")
    html = demo_seed.client.get(f"/ui/drafts/{draft['id']}").body.decode()
    for testid in ("draft-frequency", "draft-currency"):
        tag = html.split(f'data-testid="{testid}"', 1)[1].split(">", 1)[0]
        assert "readonly" in tag and 'aria-readonly="true"' in tag, testid
