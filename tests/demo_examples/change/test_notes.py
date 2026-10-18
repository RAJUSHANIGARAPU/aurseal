"""A note on a subscription keeps its title, through autosave, read-back and submit."""

from __future__ import annotations

import pytest

from tests.demo_examples.change.support import active_subscription, current


@pytest.mark.parametrize("plan_kind", ["TEAM", "INDIVIDUAL"])
def test_note_keeps_title(demo_seed, demo_control, plan_kind):
    sub = active_subscription(
        demo_seed, demo_control, now="2026-05-25", start="2026-04-01", plan_kind=plan_kind
    )
    title, description = f"Call-back request ({plan_kind.lower()})", "Prefers mornings."

    draft = demo_seed.draft(sub["id"], "note")
    assert draft["steps"] == ["note"] and not draft["title"]
    demo_seed.client.ok("PATCH", f"/api/drafts/{draft['id']}", {"title": title})
    saved = demo_seed.client.ok("PATCH", f"/api/drafts/{draft['id']}", {"description": description})
    assert saved["title"] == title, "a later autosave must not drop the title"
    assert demo_seed.client.ok("GET", f"/api/drafts/{draft['id']}")["title"] == title

    response = demo_seed.client.post(f"/api/drafts/{draft['id']}/actions/confirm")
    assert 200 <= response.status < 300, response.body[:200]

    notes = demo_seed.client.ok("GET", f"/api/subscriptions/{sub['id']}/notes")
    assert [(n["title"], n["description"]) for n in notes] == [(title, description)]
    after = current(demo_seed, sub["id"])
    assert (after["status"], after["version"]) == (sub["status"], sub["version"]), (
        "a note never changes status or version"
    )


def test_note_without_title_is_refused(demo_seed, demo_control):
    sub = active_subscription(demo_seed, demo_control, now="2026-05-25", start="2026-04-01")
    draft = demo_seed.draft(sub["id"], "note", description="No title given.")
    refused = demo_seed.client.post(f"/api/drafts/{draft['id']}/actions/confirm")
    assert refused.status == 422 and "title" in refused.json()["fields"]
    assert demo_seed.client.ok("GET", f"/api/subscriptions/{sub['id']}/notes") == []
