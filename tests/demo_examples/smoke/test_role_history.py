"""User role history: every ASSIGNED / REVOKED event names the roles it changed.

Read-only over the users' role history in a time window. With no role event in the window
the check abstains (skip): an empty history is not evidence.
"""

from __future__ import annotations

import pytest

from demo_sut.client import DemoClient
from demo_sut.server import DemoServer
from tests.demo_examples import detector_pair


def role_events(read_only, since, until):
    users = read_only.get("/api/users").json()
    events = []
    for user in users:
        response = read_only.get(
            f"/api/users/{user['id']}/role-history", params={"since": since, "until": until}
        )
        assert response.status_code == 200, f"role history of {user['id']}: {response.status_code}"
        events += [{**e, "username": user["username"]} for e in response.json()]
    return [e for e in events if e["type"] in ("ASSIGNED", "REVOKED")]


def assert_roles_named(events, faults):
    empty = [e for e in events if not e.get("roles")]
    assert not empty, f"role events without roles (active faults: {faults}): {empty}"


def change_roles(demo_sut):
    demo_sut.client.ok("POST", "/api/users/2/roles", {"assign": ["AUDITOR"]})
    demo_sut.client.ok("POST", "/api/users/2/roles", {"revoke": ["AUDITOR"]})


def test_role_events_name_roles(demo_sut, demo_control, read_only):
    demo_control.freeze("2031-05-01T10:00:00Z")
    change_roles(demo_sut)
    events = role_events(read_only, "2031-05-01T00:00:00Z", "2031-05-02T00:00:00Z")
    if not events:
        pytest.skip("no role event in the window; nothing to judge")
    assert {e["type"] for e in events} == {"ASSIGNED", "REVOKED"}
    assert_roles_named(events, read_only.get("/api/health").headers.get("X-Demo-Faults"))


def test_no_role_event_in_the_window_abstains(demo_sut, demo_control, read_only):
    events = role_events(read_only, "2031-04-01T00:00:00Z", "2031-04-01T00:00:01Z")
    assert events == []  # the case the test above turns into a skip, not a pass


@pytest.fixture(scope="module")
def low_id_demo(tmp_path_factory):
    """A private demo: its role events start at low ids, so event 10 follows event 9."""
    server = DemoServer(db_path=tmp_path_factory.mktemp("roles") / "demo.sqlite").start()
    try:
        yield DemoClient(server.base_url)
    finally:
        server.stop()


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_role_history_is_chronological_detector(low_id_demo, demo_control, fault_on):
    client = low_id_demo
    client.reset()  # a fresh database per side of the pair
    client.replace_faults(["role_history_sorted_as_text"] if fault_on else [])
    user = client.ok("GET", "/api/users")[1]
    for _ in range(5):
        client.ok("POST", f"/api/users/{user['id']}/roles", {"assign": ["AUDITOR"]})
        client.ok("POST", f"/api/users/{user['id']}/roles", {"revoke": ["AUDITOR"]})
    events = client.ok("GET", f"/api/users/{user['id']}/role-history")
    ids = [e["id"] for e in events]
    demo_control.seen_faults.update(client.state()["faults"])

    def exercised() -> None:
        # with nine events or fewer both orders agree; eleven ids cross from 9 to 10
        assert len(ids) >= 11 and min(ids) < 10 <= max(ids), f"precondition not met: {ids}"

    def oracle() -> None:
        assert ids == sorted(ids) and [e["at"] for e in events] == sorted(
            e["at"] for e in events
        ), f"DEMO-113: role history is not in time order: ids {ids}"

    try:
        detector_pair.judge(
            LowIdControl(client, demo_control),
            ticket="DEMO-113",
            signature=r"DEMO-113: role history is not in time order",
            oracle=oracle,
            exercised=exercised,
            fault="role_history_sorted_as_text",
            fault_on=fault_on,
        )
    finally:
        client.replace_faults([])


class LowIdControl:
    """The pair's view of the private demo: its client for the fault state, the session's
    control for the evidence the healthy side checks."""

    def __init__(self, client, session_control) -> None:
        self.client = client
        self.session_control = session_control

    def evidence(self) -> str:
        return ",".join(self.client.state()["faults"]) or "none"
