"""Allure categories, environment/executor files, step screenshots and the session plugin."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from reporting.allure_categories import DEFAULT_CATEGORIES, categorize, write_categories
from reporting.allure_environment import (
    environment_rows,
    executor_info,
    properties_text,
    write_environment,
)
from reporting.step_screenshots import StepScreenshotListener, enabled, set_current_page

pytest_plugins = ["pytester"]
ROOT = Path(__file__).resolve().parents[2]


class TestCategories:
    @pytest.mark.parametrize(
        "status, message, expected",
        [
            (
                "failed",
                "[XPASS(strict)] known defect BUG-142 is open: rounding. "
                "If this passed, BUG-142 looks fixed",
                "Known defect no longer reproduces",
            ),
            (
                "skipped",
                "XFAIL known defect BUG-142 is open: rounding",
                "Known defect still reproduces",
            ),
            ("failed", "requests.exceptions.HTTPError: HTTP 503 on POST /orders", "Backend 5xx"),
            (
                "broken",
                "Error: net::ERR_CONNECTION_REFUSED at https://app.example.test",
                "Backend 5xx",
            ),
            ("failed", "AssertionError: expected 200, got 401", "Authentication"),
            (
                "broken",
                "KeyError: 'Missing config key: base_url (env=qa, or set BASE_URL)'",
                "Configuration",
            ),
            (
                "failed",
                "Error: strict mode violation: get_by_role('button') resolved to 2 elements",
                "Broken locators",
            ),
            ("failed", "TimeoutError: Locator.click: Timeout 30000ms exceeded.", "Timeouts"),
            (
                "skipped",
                "blocked by BUG-77: export drops the header row",
                "Skipped: known product bug",
            ),
            (
                "skipped",
                "Skipped: no test data on this environment",
                "Skipped: environment limitation",
            ),
            ("skipped", "superseded by test_checkout_v2", "Skipped: superseded"),
            ("skipped", "on-demand: set ENABLE_SLOW_SUITE=true", "Skipped: harness"),
            (
                "broken",
                "AttributeError: 'NoneType' object has no attribute 'click'",
                "Test or harness errors",
            ),
            ("failed", "AssertionError: total was 9.99, expected 10.00", "Product defects"),
        ],
    )
    def test_buckets(self, status, message, expected):
        assert (categorize(status, message) or "").startswith(expected)

    def test_multi_line_messages_still_match(self):
        message = "AssertionError: request failed\n  POST /orders -> HTTP 502\n  at line 3"
        assert categorize("failed", message).startswith("Backend 5xx")

    def test_an_unmatched_skip_is_uncategorised(self):
        assert categorize("skipped", "plain reason") is None

    def test_written_file_is_valid_and_complete(self, tmp_path):
        path = write_categories(tmp_path)
        assert json.loads(path.read_text()) == DEFAULT_CATEGORIES

    def test_a_bad_pattern_fails_at_write_time(self, tmp_path):
        with pytest.raises(re.error):
            write_categories(
                tmp_path, [{"name": "x", "messageRegex": "(", "matchedStatuses": ["failed"]}]
            )


class TestEnvironmentFiles:
    def test_rows_prefer_ci_variables(self):
        rows = environment_rows(
            env="staging",
            base_url="https://staging.example.test",
            browsers=["chromium", "firefox"],
            headed=False,
            auth_mode="ui",
            run_id="gh-1-1",
            extra={"Specs.Commit": "0123abcd"},
            environ={
                "GITHUB_ACTIONS": "true",
                "GITHUB_REF_NAME": "main",
                "GITHUB_SHA": "fedcba9876543210",
            },
        )
        assert rows["Environment"] == "staging"
        assert rows["Browser"] == "chromium, firefox"
        assert rows["Headless"] == "true"
        assert rows["Git.Branch"] == "main" and rows["Git.Commit"] == "fedcba98"
        assert rows["Run.Mode"] == "CI"
        assert rows["Specs.Commit"] == "0123abcd"

    def test_properties_escaping(self):
        text = properties_text({"Base URL": "https://x.example.test:8443/a=b"})
        assert text == "Base\\ URL=https\\://x.example.test\\:8443/a\\=b\n"

    def test_github_executor_links_the_run(self):
        info = executor_info(
            "Test run - qa",
            {
                "GITHUB_RUN_ID": "991",
                "GITHUB_RUN_NUMBER": "57",
                "GITHUB_SERVER_URL": "https://github.example.test",
                "GITHUB_REPOSITORY": "team/app",
                "GITHUB_WORKFLOW": "Tests",
            },
        )
        assert info["type"] == "github" and info["buildOrder"] == 57
        assert info["buildUrl"] == "https://github.example.test/team/app/actions/runs/991"
        assert info["buildName"] == "Tests #57"

    def test_jenkins_executor(self):
        info = executor_info(
            "r",
            {"BUILD_NUMBER": "12", "BUILD_URL": "https://ci.example.test/12/", "JOB_NAME": "app"},
        )
        assert (info["type"], info["buildName"], info["buildOrder"]) == ("jenkins", "app #12", 12)

    def test_local_executor(self):
        assert executor_info("r", {})["buildName"].startswith("local @ ")

    def test_write_never_raises(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("")
        assert write_environment(blocker / "sub", {"a": "b"}, {}) == []
        written = write_environment(tmp_path / "results", {"a": "b"}, {"name": "Local"})
        assert [p.name for p in written] == ["environment.properties", "executor.json"]


class _Page:
    def __init__(self, fail=False):
        self.fail = fail

    def screenshot(self):
        if self.fail:
            raise RuntimeError("closed")
        return b"png"


class TestStepScreenshots:
    def test_attaches_inside_the_step_when_a_page_is_set(self):
        attached = []
        listener = StepScreenshotListener(attach=lambda png, title: attached.append((png, title)))
        set_current_page(_Page())
        try:
            listener.start_step("u1", "Open the basket", {})
            listener.stop_step("u1", None, None, None)
        finally:
            set_current_page(None)
        assert attached == [(b"png", "Open the basket")]

    def test_no_page_and_failed_capture_are_no_ops(self):
        attached = []
        listener = StepScreenshotListener(attach=lambda png, title: attached.append(title))
        listener.stop_step("u2", None, None, None)
        set_current_page(_Page(fail=True))
        try:
            listener.stop_step("u3", None, None, None)
        finally:
            set_current_page(None)
        assert attached == []

    def test_off_unless_asked(self):
        assert not enabled({}) and enabled({"ALLURE_STEP_SCREENSHOTS": "true"})

    def test_hooks_run_inside_a_real_allure_step(self, pytester, monkeypatch):
        """tryfirst must put the attachment inside the step, before allure-pytest closes it."""
        monkeypatch.setenv("PYTHONPATH", str(ROOT))
        monkeypatch.setenv("ALLURE_STEP_SCREENSHOTS", "true")
        pytester.makeini("[pytest]\n")
        pytester.makeconftest(
            """
            from reporting import step_screenshots

            class Page:
                def screenshot(self):
                    return b"\\x89PNG"

            def pytest_configure(config):
                step_screenshots.register_if_enabled()

            def pytest_runtest_setup(item):
                step_screenshots.set_current_page(Page())
            """
        )
        pytester.makepyfile(
            """
            import allure

            def test_steps():
                with allure.step("first step"):
                    pass
            """
        )
        results = pytester.path / "allure"
        result = pytester.runpytest_subprocess("-p", "no:cacheprovider", f"--alluredir={results}")
        result.assert_outcomes(passed=1)
        test_result = next(json.loads(p.read_text()) for p in results.glob("*-result.json"))
        step = test_result["steps"][0]
        assert step["name"] == "first step"
        assert [a["name"] for a in step.get("attachments", [])] == ["first step"]


class TestSessionPlugin:
    def _run(self, pytester, monkeypatch, *args):
        monkeypatch.setenv("PYTHONPATH", str(ROOT))
        for name in ("TEST_RUN_ID", "BUILD_TAG", "GITHUB_RUN_ID", "BUILD_NUMBER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("TEST_RUN_ID", "run-77")
        pytester.makeini("[pytest]\n")
        pytester.makepyfile("def test_one():\n    pass\n\ndef test_two():\n    pass\n")
        results = pytester.path / "allure"
        result = pytester.runpytest_subprocess(
            "-p",
            "no:cacheprovider",
            "-p",
            "reporting.pytest_plugin",
            f"--alluredir={results}",
            *args,
        )
        result.assert_outcomes(passed=2)
        return results

    def test_controller_writes_the_three_files(self, pytester, monkeypatch):
        results = self._run(pytester, monkeypatch)
        properties = (results / "environment.properties").read_text()
        assert "Run.Id=run-77" in properties
        assert "Python.Version=" in properties
        assert json.loads((results / "executor.json").read_text())["name"] in (
            "Local",
            "GitHub Actions",
            "Jenkins",
        )
        assert json.loads((results / "categories.json").read_text()) == DEFAULT_CATEGORIES

    def test_works_under_xdist(self, pytester, monkeypatch):
        results = self._run(pytester, monkeypatch, "-n", "2")
        assert (results / "environment.properties").read_text().count("Run.Id=run-77") == 1

    def test_workers_never_write(self, tmp_path):
        """Only the controller writes; a worker's config carries ``workerinput``."""
        from types import SimpleNamespace

        from reporting.pytest_plugin import ReportMetadataPlugin, is_controller

        worker = SimpleNamespace(workerinput={}, getoption=lambda name: str(tmp_path))
        assert not is_controller(worker)
        plugin = ReportMetadataPlugin()
        plugin.pytest_sessionstart(SimpleNamespace(config=worker))
        assert plugin.written == [] and list(tmp_path.iterdir()) == []
