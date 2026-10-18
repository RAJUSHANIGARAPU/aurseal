"""test-health-report: the gate, the healing window, saving and printing, the down stop."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import health

GREEN = (
    '<testsuite><testcase classname="t" name="a" time="0.4"/>'
    '<testcase classname="t" name="b" time="2.5"/></testsuite>'
)
RED = (
    '<testsuite><testcase classname="t" name="a" time="0.4"/>'
    '<testcase classname="t.Search" name="test_results" time="1">'
    '<failure message="AssertionError: expected 3 results">x</failure></testcase></testsuite>'
)


def runner_writing(path, xml, console="ok"):
    def run(cmd):
        assert f"--junitxml={path}" in cmd
        path.write_text(xml, encoding="utf-8")
        return console

    return run


def go(tmp_path, xml, log=None, console="ok"):
    junit_path = tmp_path / "junit.xml"
    return health.run(
        ["-m", "smoke"],
        env="qa",
        out_dir=tmp_path / "out",
        log=log or tmp_path / "missing.jsonl",
        junit_path=junit_path,
        probe=lambda: (True, "up"),
        runner=runner_writing(junit_path, xml, console),
        branch="feat/x",
    )


def test_all_green_without_a_healing_log_is_pass_with_healing_not_measured(tmp_path):
    code, text = go(tmp_path, GREEN)
    assert code == 0
    assert "**PASS**" in text and "Healing was not measured" in text
    assert "| 2 | 0 | 0 | not measured |" in text


def test_one_failure_blocks_and_is_listed(tmp_path):
    code, text = go(tmp_path, RED)
    assert code == 1 and "**BLOCK**" in text
    assert "`t.Search::test_results` (failure): AssertionError: expected 3 results" in text


def heal_log(tmp_path, *offsets):
    log = tmp_path / "heal.jsonl"
    now = datetime.now(tz=timezone.utc)
    lines = [
        json.dumps(
            {
                "timestamp": (now + timedelta(seconds=o)).isoformat(),
                "test": f"t{i}",
                "applied": True,
                "strategy": "locator",
                "failure_type": "locator",
                "patched_files": ["ui/locators/wiki_locators.json"],
            }
        )
        for i, o in enumerate(offsets)
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


def test_a_heal_after_the_start_on_a_green_run_warns(tmp_path):
    log = tmp_path / "heal.jsonl"
    junit_path = tmp_path / "junit.xml"

    def run(cmd):
        junit_path.write_text(GREEN, encoding="utf-8")
        log.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    "test": "t",
                    "applied": True,
                    "strategy": "s",
                    "failure_type": "f",
                }
            )
            + "\n"
        )
        return "ok"

    code, text = health.run(
        [],
        env="qa",
        out_dir=tmp_path / "o",
        log=log,
        junit_path=junit_path,
        probe=lambda: (True, "up"),
        runner=run,
        branch="b",
    )
    assert code == 0 and "**WARN**" in text and "| 1 |" in text.split("## Summary")[1]


def test_heals_older_than_the_run_start_are_excluded(tmp_path):
    code, text = go(tmp_path, GREEN, log=heal_log(tmp_path, -3600, -60))
    assert "**PASS**" in text and "not measured" in text


def test_the_report_is_saved_with_a_timestamp_and_printed(tmp_path):
    _, text = go(tmp_path, GREEN)
    saved = list((tmp_path / "out").glob("test-health-*.md"))
    assert len(saved) == 1 and saved[0].stem[len("test-health-") :].replace("-", "").isdigit()
    assert saved[0].read_text(encoding="utf-8") in text


def test_an_unreachable_app_stops_before_running(tmp_path):
    ran = []
    code, text = health.run(
        [],
        env="qa",
        out_dir=tmp_path / "o",
        probe=lambda: (False, "refused"),
        runner=lambda cmd: ran.append(cmd) or "",
        branch="b",
    )
    assert code == 1 and "down" in text and ran == []
    assert not (tmp_path / "o").exists()


def test_retried_and_slowest_tests_are_reported(tmp_path):
    console = "ui/tests/test_a.py::test_x RERUN\nui/tests/test_a.py::test_x PASSED\n"
    _, text = go(tmp_path, GREEN, console=console)
    assert "Retried: `ui/tests/test_a.py::test_x`" in text
    assert text.index("t::b") < text.index("t::a", text.index("Slowest"))


def test_an_unreadable_xml_is_reported_not_fatal(tmp_path):
    code, text = go(tmp_path, "<testsuite")
    assert code == 1 and "unreadable report" in text


def test_the_command_may_be_model_invoked_and_states_the_rules():
    meta, body = read_command("test-health-report")
    flat = " ".join(body.split())
    assert "disable-model-invocation" not in meta
    assert claude_md_lists("test-health-report")
    for promise in (
        "stops before running anything",
        "never zero",
        "**Block**",
        "**Warn**",
        "**Pass**",
        "only the healing orchestrator writes this log",
    ):
        assert promise in flat, promise
