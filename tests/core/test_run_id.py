"""Test-traffic tagging: one run id per run, carried in the User-Agent and a header."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core import run_id

NOW = datetime(2026, 3, 4, 5, 6, 7)
ROOT = Path(__file__).resolve().parents[2]

pytest_plugins = ["pytester"]


class TestResolution:
    def test_explicit_id_wins(self):
        environ = {"TEST_RUN_ID": "nightly-7", "BUILD_TAG": "jenkins-x-1"}
        assert run_id.resolve(environ, "host", NOW) == "nightly-7"

    def test_jenkins_build_tag(self):
        assert run_id.resolve({"BUILD_TAG": "jenkins-job-42"}, "host", NOW) == "jenkins-job-42"

    def test_github_actions_run_and_attempt(self):
        environ = {"GITHUB_RUN_ID": "991", "GITHUB_RUN_ATTEMPT": "2"}
        assert run_id.resolve(environ, "host", NOW) == "gh-991-2"

    def test_local_fallback_uses_the_short_host_and_time(self):
        assert run_id.resolve({}, "laptop.example.test", NOW) == "local-laptop-20260304-050607"

    def test_local_fallback_without_a_host(self):
        assert run_id.resolve({}, "", NOW) == "local-20260304-050607"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("  my run/branch #3 ", "my-run-branch-3"),
            ("--x--", "x"),
            ("a" * 80, "a" * 64),
            ("!!!", ""),
            (None, ""),
        ],
    )
    def test_sanitize(self, raw, expected):
        assert run_id.sanitize(raw) == expected


class TestCarriers:
    def test_api_headers(self):
        environ = {"TEST_RUN_ID": "r1", "TEST_RUN_PRODUCT": "shop-qa"}
        assert run_id.api_headers(environ) == {
            "User-Agent": "shop-qa/r1 python-requests",
            "X-Test-Run-Id": "r1",
        }

    def test_no_headers_when_tagging_is_off(self):
        assert run_id.api_headers({"TEST_RUN_ID": "r1", "TEST_RUN_TAGGING": "false"}) == {}

    def test_browser_agent_is_appended_to_never_replaced(self):
        tagged = run_id.browser_user_agent("Mozilla/5.0 Chrome/140", {"TEST_RUN_ID": "r1"})
        assert tagged == "Mozilla/5.0 Chrome/140 test-automation/r1"

    @pytest.mark.parametrize("default", [None, "", "  "])
    def test_unknown_browser_agent_leaves_the_default(self, default):
        assert run_id.browser_user_agent(default, {"TEST_RUN_ID": "r1"}) is None

    def test_tagged_user_agent_reads_the_real_one_and_closes_its_probe(self):
        closed = []

        class Page:
            def evaluate(self, script):
                return "Mozilla/5.0 Real"

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                closed.append(True)

        class Browser:
            def new_context(self):
                return Context()

        assert run_id.tagged_user_agent(Browser(), {"TEST_RUN_ID": "r9"}) == (
            "Mozilla/5.0 Real test-automation/r9"
        )
        assert closed == [True]


class TestOneIdPerRun:
    def test_the_process_id_is_stable_across_calls(self, monkeypatch):
        monkeypatch.delenv("TEST_RUN_ID", raising=False)
        monkeypatch.delenv("BUILD_TAG", raising=False)
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
        first = run_id.run_id()
        assert run_id.run_id() == first

    def test_pin_exports_the_id(self):
        environ = {"BUILD_TAG": "ci-5"}
        assert run_id.pin_for_session(environ) == "ci-5"
        assert environ["TEST_RUN_ID"] == "ci-5"

    def test_xdist_workers_share_the_controller_id(self, pytester, monkeypatch):
        """Each worker is a process; without pinning each would mint its own local id."""
        for name in ("TEST_RUN_ID", "BUILD_TAG", "GITHUB_RUN_ID"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("PYTHONPATH", str(ROOT))
        pytester.makeini("[pytest]\n")
        pytester.makeconftest(
            """
            import os
            from core import run_id

            def pytest_configure(config):
                if not hasattr(config, "workerinput"):
                    run_id.pin_for_session()
            """
        )
        pytester.makepyfile(
            **{
                f"test_w{i}": f"""
                import os
                from core import run_id

                def test_{i}():
                    inherited = os.environ.get("TEST_RUN_ID", "")
                    with open(os.path.join({str(pytester.path)!r}, "ids-{i}.txt"), "w") as f:
                        f.write(inherited + "|" + run_id.run_id())
                """
                for i in range(4)
            }
        )
        result = pytester.runpytest_subprocess("-n", "2", "-p", "no:cacheprovider")
        result.assert_outcomes(passed=4)
        lines = {(pytester.path / f"ids-{i}.txt").read_text() for i in range(4)}
        assert len(lines) == 1, lines
        inherited, used = lines.pop().split("|")
        # Inherited from the controller, not minted by the worker a second later.
        assert inherited and inherited == used
