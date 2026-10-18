"""Clock and fault control, fault reporting, fixture behaviour, isolation and the CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from demo_sut import DemoClient, DemoServer
from demo_sut.clock import DemoClock
from demo_sut.faults import FAULTS, FaultSwitches, UnknownFault

pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[2]


# -- clock --------------------------------------------------------------------------


def test_clock_freeze_shift_reset():
    clock = DemoClock()
    clock.freeze("2026-02-28T23:00:00Z")
    clock.shift(days=1)
    assert clock.now().isoformat() == "2026-03-01T23:00:00+00:00"
    assert clock.describe()["mode"] == "frozen"
    clock.reset()
    assert clock.describe()["mode"] == "real"
    clock.shift(days=-2)
    assert clock.describe()["offset_seconds"] == -2 * 86400


def test_clock_is_reported_on_every_response(demo_sut, demo_control):
    demo_control.freeze("2026-07-01T12:00:00Z")
    response = demo_sut.client.get("/api/plans")
    assert response.headers["x-demo-clock"] == "2026-07-01T12:00:00.000Z; frozen"
    demo_control.shift(days=1)
    assert demo_sut.client.ok("GET", "/api/_admin/clock")["now"] == "2026-07-02T12:00:00.000Z"
    assert demo_sut.client.post("/api/_admin/clock", {"now": "not a date"}).status == 400


# -- faults ---------------------------------------------------------------------------


def test_fault_switches_unit():
    switches = FaultSwitches()
    assert switches.header() == "none"
    switches.set({"rounding_drift": True, "xml_escapes_twice": True})
    assert switches.header() == "rounding_drift,xml_escapes_twice"
    switches.replace(["api_unavailable"])
    assert switches.active() == ["api_unavailable"]
    with pytest.raises(UnknownFault):
        switches.set({"no_such_fault": True})
    with pytest.raises(UnknownFault):
        switches.is_on("no_such_fault")
    assert len(switches.catalogue()) == len(FAULTS)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/plans"),
        ("GET", "/api/plans/999999"),
        ("GET", "/api/no-such-route"),
        ("GET", "/gw/no-such-route"),
        ("GET", "/ui"),
        ("GET", "/ui/missing"),
        ("POST", "/api/render"),
        ("GET", "/api/templates/invoice/source"),
    ],
)
def test_active_faults_are_reported_on_every_kind_of_response(demo_sut, demo_control, method, path):
    demo_control.faults("rounding_drift")
    body = {"template": "invoice", "payload": {}} if method == "POST" else None
    response = demo_sut.client.request(method, path, body)
    assert response.headers["x-demo-faults"] == "rounding_drift"
    demo_control.faults()
    assert demo_sut.client.request(method, path, body).headers["x-demo-faults"] == "none"


def test_unknown_fault_name_is_refused(demo_sut, demo_control):
    response = demo_sut.client.post("/api/_admin/faults", {"no_such_fault": True})
    assert response.status == 400 and "rounding_drift" in response.json()["known"]


def test_settings_are_validated(demo_sut, demo_control):
    assert demo_sut.client.patch("/api/_admin/settings", {"bogus": 1}).status == 400
    assert demo_sut.client.patch("/api/_admin/settings", {"send_order": "v9"}).status == 400


# -- fixtures (run in an inner pytest session) ------------------------------------------


def test_control_fixture_restores_state_and_records_fault_evidence(pytester):
    pytester.makepyfile(
        """
        from demo_sut.pytest_plugin import demo_control, demo_sut  # noqa: F401

        def test_a(demo_sut, demo_control):
            demo_control.freeze("2026-01-01T00:00:00Z")
            demo_control.fault("rounding_drift")
            demo_control.settings(send_order="v2")

        def test_b(demo_sut, demo_control):
            state = demo_sut.client.state()
            assert state["faults"] == []
            assert state["clock"]["mode"] == "real"
            assert state["settings"]["send_order"] == "v1"
        """
    )
    result = pytester.runpytest_inprocess(
        "-p", "no:cacheprovider", "-o", "addopts=", "--junitxml=out.xml"
    )
    result.assert_outcomes(passed=2)
    xml = (pytester.path / "out.xml").read_text()
    assert 'name="demo_faults" value="rounding_drift"' in xml
    assert 'name="demo_faults" value="none"' in xml


def test_http_engine_fixture_feeds_fault_evidence(demo_sut, demo_control, demo_http):
    demo_control.client.set_faults(xml_escapes_twice=True)  # bypasses the control's own record
    demo_control.client.observed_faults.clear()
    assert demo_http.get("plans").status == 200
    assert "xml_escapes_twice" in demo_control.seen_faults


def test_external_instance_via_env(pytester, monkeypatch):
    with DemoServer() as server:
        monkeypatch.setenv("DEMO_SUT_URL", server.base_url)
        pytester.makepyfile(
            f"""
            from demo_sut.pytest_plugin import demo_sut  # noqa: F401

            def test_points_at_external(demo_sut):
                assert demo_sut.server is None
                assert demo_sut.base_url == "{server.base_url}"
                assert demo_sut.client.ok("GET", "/api/health") == {{"status": "ok"}}
            """
        )
        result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-o", "addopts=")
        result.assert_outcomes(passed=1)


# -- isolation ---------------------------------------------------------------------------


def test_each_instance_has_its_own_port_and_database():
    with DemoServer() as one, DemoServer() as two:
        assert one.port != two.port and one.db_path != two.db_path
        first, second = DemoClient(one.base_url), DemoClient(two.base_url)
        first.ok("POST", "/api/tasks", {"title": "only in one"})
        assert [t["title"] for t in first.ok("GET", "/api/tasks")] == ["only in one"]
        assert second.ok("GET", "/api/tasks") == []
        first.set_faults(rounding_drift=True)
        assert second.get("/api/plans").faults == []


def test_session_fixture_uses_a_private_database(demo_sut):
    assert demo_sut.server is not None
    assert demo_sut.db_path.is_file()
    assert demo_sut.base_url.startswith("http://127.0.0.1:")


def test_reset_restores_seed_data(demo_control):
    with DemoServer() as server:
        client = DemoClient(server.base_url)
        client.ok("POST", "/api/tasks", {"title": "temporary"})
        client.set_faults(rounding_drift=True)
        client.reset()
        assert client.ok("GET", "/api/tasks") == []
        assert client.state()["faults"] == []
        assert len(client.ok("GET", "/api/plans")) == 2


def test_database_file_is_readable_read_only(demo_sut, demo_seed):
    import sqlite3

    demo_seed.subscription()
    conn = sqlite3.connect(f"file:{demo_sut.db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT number, status FROM subscriptions").fetchall()
        assert rows and all(number.startswith("SUB-") for number, _ in rows)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM subscriptions")
    finally:
        conn.close()


# -- CLI ------------------------------------------------------------------------------------


def test_cli_serves_on_the_requested_port(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "demo_sut", "--port", "0", "--db", str(tmp_path / "cli.sqlite")],
        cwd=REPO,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        assert line.startswith("demo_sut listening on http://127.0.0.1:")
        url = line.split()[3]
        with urllib.request.urlopen(url + "/api/health", timeout=5) as response:
            assert json.loads(response.read()) == {"status": "ok"}
            assert response.headers["X-Demo-Faults"] == "none"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_cli_prints_the_openapi_document():
    out = subprocess.run(
        [sys.executable, "-m", "demo_sut", "openapi"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out)["info"]["title"] == "Demo subscription service"
