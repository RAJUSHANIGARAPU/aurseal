"""The demo subscription service as a ``generators.config_suite.MachineAdapter``.

Transitions are driven over the demo's API (one call per action, the way the screen's own
buttons call it) and every check reads the browser: the screen must show
``[data-state="<state>"]`` on the status pill, and ``[data-action="<name>"]`` for an action.
The clock is frozen; before a renewal it is moved to 30 days before the term end and every
period due is booked, so a renewal is always allowed (also a second one after a renewal).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from generators.state_graph import TraversalPath
from generators.workflow_model import Transition

ROOT = Path(__file__).resolve().parents[3]
MACHINE_FILE = ROOT / "demo_sut" / "config" / "subscription.machine.yaml"
NOW = "2026-03-10T09:00:00Z"
DRAFT_KINDS = ("change", "cancel", "renew")
#: The shortest way into each state, as the actions to run from a new draft.
ROUTES = {
    "draft": (),
    "pending_payment": ("submit",),
    "active": ("submit", "confirm"),
    "renewed": ("submit", "confirm", "renew", "confirm_renew"),
    "cancelled": ("submit", "confirm", "cancel", "confirm_cancel"),
}


def feasible(path: TraversalPath) -> bool:
    """Paths the demo can walk: a draft is confirmed by its own kind's confirm (or discarded).

    The machine file states this as conditions (``draft.kind == 'cancel'``) that refer to the
    action that opened the draft, which a graph walk cannot evaluate on its own.
    """
    opened = None
    for action in path.actions:
        if action in DRAFT_KINDS:
            opened = action
        elif action.startswith("confirm_") and action != f"confirm_{opened}":
            return False
    return True


class DemoMachine:
    def __init__(self, page: Any, demo_sut: Any, demo_seed: Any, demo_control: Any) -> None:
        self.page = page
        self.base_url = demo_sut.base_url
        self.client = demo_sut.client
        self.seed = demo_seed
        self.control = demo_control
        demo_control.freeze(NOW)
        self.sub_id: int | None = None

    # -- MachineAdapter -----------------------------------------------------------------------

    def fetch_machine(self, key: str) -> dict[str, Any] | None:
        response = self.client.get(f"/api/machines/{key}")
        return response.json() if response.status == 200 else None

    def open_in_state(self, state: str, action: str | None = None) -> None:
        if state == "changing":
            kind = (
                action.removeprefix("confirm_")
                if action and action.startswith("confirm_")
                else "change"
            )
            self._new(activate=False)
            for step in ("submit", "confirm", kind):
                self._run(step)
            self.page.goto(f"{self.base_url}/ui/drafts/{self._open_draft()}")
            return
        self._new(activate=action == "activate")
        for step in ROUTES[state]:
            self._run(step)
        self._open_detail()

    def rendered_actions(self, action: str) -> int:
        return self.page.locator(f'[data-action="{action}"]').count()

    def start(self, path: TraversalPath) -> None:
        self._new(activate=bool(path.actions) and path.actions[0] == "activate")
        self._open_detail()

    def drive(self, source: str, transition: Transition) -> None:
        self._run(transition.action)

    def shows_state(self, state: str) -> bool:
        self._open_detail()
        pill = self.page.locator(f'[data-testid="status-pill"][data-state="{state}"]')
        try:
            pill.wait_for(state="visible", timeout=5000)
        except Exception:  # noqa: BLE001 - playwright timeout: the state is not shown
            return False
        return True

    # -- driving the demo ---------------------------------------------------------------------

    def _new(self, *, activate: bool) -> None:
        sub = self.seed.subscription(state="draft", start_date="2025-03-15", frequency="YEARLY")
        self.sub_id = sub["id"]
        if activate:
            self.client.ok(
                "PATCH", f"/api/subscriptions/{self.sub_id}", {"activate_immediately": True}
            )

    def _into_renewal_window(self) -> None:
        """Move the clock to 30 days before the term end (if earlier) and book what is due."""
        sub = f"/api/subscriptions/{self.sub_id}"
        latest = self.client.ok("GET", f"{sub}/versions")[-1]["version"]
        # the latest version, not the current one: a renewal may not be in effect yet
        term_end = self.client.ok("GET", f"{sub}?version={latest}")["term_end"]
        today = self.client.ok("GET", "/api/_admin/clock")["now"][:10]
        gap = (date.fromisoformat(term_end) - date.fromisoformat(today)).days - 30
        if gap > 0:
            self.control.shift(days=gap)
        self.seed.book_due(self.sub_id)

    def _open_detail(self) -> None:
        self.page.goto(f"{self.base_url}/ui/subscriptions/{self.sub_id}")

    def _open_draft(self) -> int:
        return self.client.ok("GET", f"/api/subscriptions/{self.sub_id}")["open_draft_id"]

    def _run(self, action: str) -> None:
        sub = f"/api/subscriptions/{self.sub_id}"
        if action == "submit":
            self.client.ok("POST", f"{sub}/actions/submit", {})
        elif action in ("confirm", "activate"):
            self.client.ok("POST", f"{sub}/actions/confirm")
        elif action in DRAFT_KINDS:
            if action == "renew":
                self._into_renewal_window()
            body = {
                "kind": action,
                **({"reason": "NO_LONGER_NEEDED"} if action == "cancel" else {}),
            }
            self.client.ok("POST", f"{sub}/drafts", body)
        elif action == "note":
            draft = self.seed.draft(
                self.sub_id, "note", title="Call back", description="Asked for a quote"
            )
            self.seed.confirm_draft(draft["id"])
        elif action.startswith("confirm_"):
            self.client.ok("POST", f"/api/drafts/{self._open_draft()}/actions/confirm")
        elif action == "discard_draft":
            self.client.ok("POST", f"/api/drafts/{self._open_draft()}/actions/discard")
        else:
            raise AssertionError(f"the demo driver has no step for action {action!r}")
