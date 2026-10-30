"""Per-test browser evidence, the trace/video retention policy and the failure bundle."""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

from core.artifact_policy import RetentionPolicy, finalize_videos, finish_trace, page_videos
from core.browser_evidence import BrowserEvidence
from core.failure_reporter import MAX_STACK_CHARS, format_stack, write_failure_bundle

pytest_plugins = ["pytester"]
ROOT = Path(__file__).resolve().parents[2]


class _Emitter:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, payload) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


class _Context(_Emitter):
    def __init__(self, pages=()) -> None:
        super().__init__()
        self.pages = list(pages)


def _response(status, method="GET", url="https://app.example.test/api/x"):
    return SimpleNamespace(status=status, url=url, request=SimpleNamespace(method=method))


class TestBrowserEvidence:
    def test_collects_from_existing_and_later_pages(self):
        first, popup = _Emitter(), _Emitter()
        context = _Context([first])
        evidence = BrowserEvidence().attach_context(context)
        context.emit("page", popup)

        first.emit("console", SimpleNamespace(type="error", text="TypeError: x is null"))
        first.emit("console", SimpleNamespace(type="log", text="ignored"))
        popup.emit("pageerror", ValueError("boom in popup"))
        popup.emit("response", _response(503, "POST"))
        popup.emit("response", _response(200))
        popup.emit(
            "requestfailed",
            SimpleNamespace(
                method="GET", url="https://cdn.example.test/a.js", failure="net::ERR_ABORTED"
            ),
        )

        assert evidence.console_errors == ["TypeError: x is null"]
        assert evidence.page_errors == ["boom in popup"]
        assert evidence.failed_requests == [
            "POST https://app.example.test/api/x -> 503",
            "GET https://cdn.example.test/a.js -> net::ERR_ABORTED",
        ]

    def test_buckets_are_bounded(self):
        evidence = BrowserEvidence(limit=3)
        for i in range(10):
            evidence.on_console(SimpleNamespace(type="error", text=str(i)))
        assert evidence.console_errors == ["0", "1", "2"]

    def test_screenshot_failure_is_swallowed(self, tmp_path):
        class Broken:
            def screenshot(self, **kwargs):
                raise RuntimeError("target closed")

        assert BrowserEvidence().screenshot(Broken(), "t", tmp_path) is None

    def test_screenshot_keeps_the_bytes(self, tmp_path):
        class Page:
            def screenshot(self, path, full_page):
                Path(path).write_bytes(b"png")
                return b"png"

        evidence = BrowserEvidence()
        path = evidence.screenshot(Page(), "test name/1", tmp_path)
        assert path.exists() and "/" not in path.name
        assert evidence.last_screenshot == b"png"


class _Tracing:
    def __init__(self) -> None:
        self.stops: list = []

    def stop(self, path=None):
        self.stops.append(path)
        if path:
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("trace.trace", "{}")


class TestTraceAndVideo:
    def test_policy(self):
        assert RetentionPolicy().keep_trace(True) and not RetentionPolicy().keep_trace(False)
        assert RetentionPolicy.from_env({"TRACE_ALWAYS": "true"}).keep_trace(False)
        assert RetentionPolicy.from_env({"VIDEO_ALWAYS": "1"}).keep_video(False)

    def test_a_passing_trace_is_discarded_without_serialising(self, tmp_path):
        context = SimpleNamespace(tracing=_Tracing())
        assert finish_trace(context, False, tmp_path / "t.zip") is None
        assert context.tracing.stops == [None]
        assert not (tmp_path / "t.zip").exists()

    def test_a_failing_trace_is_saved(self, tmp_path):
        context = SimpleNamespace(tracing=_Tracing())
        assert (
            finish_trace(context, True, tmp_path / "traces" / "t.zip")
            == tmp_path / "traces" / "t.zip"
        )

    def test_videos_are_moved_when_kept_and_deleted_otherwise(self, tmp_path):
        files = [tmp_path / "a.webm", tmp_path / "b.webm"]
        for f in files:
            f.write_bytes(b"v")
        pages = [SimpleNamespace(video=SimpleNamespace(path=lambda f=f: str(f))) for f in files]
        videos = page_videos(SimpleNamespace(pages=pages))
        kept = finalize_videos(videos[:1], True, [tmp_path / "keep" / "a.webm"])
        assert kept == [tmp_path / "keep" / "a.webm"] and kept[0].exists()
        assert finalize_videos(videos[1:], False, [tmp_path / "x.webm"]) == []
        assert not files[1].exists()


def _run(pytester, source: str):
    pytester.makeini("[pytest]\n")
    pytester.makeconftest(
        """
        import json, os, pytest
        from core import artifact_policy

        def pytest_configure(config):
            artifact_policy.register(config)

        @pytest.fixture(autouse=True)
        def verdict(request):
            yield
            with open(os.environ["ARTIFACT_KEEP_FILE"], "a") as f:
                kept = artifact_policy.worth_keeping(request.node)
                f.write(json.dumps([request.node.name, kept]) + "\\n")
        """
    )
    pytester.makepyfile(source)
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "verification.pytest_plugin"
    )
    lines = Path(os.environ["ARTIFACT_KEEP_FILE"]).read_text().splitlines()
    return result, dict(json.loads(line) for line in lines)


class TestOutcomeRecorder:
    def test_teardown_knows_what_happened(self, pytester, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", str(ROOT))
        monkeypatch.setenv("ARTIFACT_KEEP_FILE", str(pytester.path / "kept.jsonl"))
        result, verdicts = _run(
            pytester,
            """
            import pytest

            def test_passes():
                pass

            def test_fails():
                assert False

            def test_abstains():
                pytest.skip("no test data on this environment")

            @pytest.mark.known_defect("BUG-142", raises=AssertionError)
            def test_known_red():
                assert False
            """,
        )
        result.assert_outcomes(passed=1, failed=1, skipped=1, xfailed=1)
        assert verdicts == {
            "test_passes": False,
            "test_fails": True,
            "test_abstains": True,
            "test_known_red": False,
        }


class TestFailureBundle:
    def _raised(self) -> BaseException:
        try:
            raise AssertionError("expected 3 items, got 2")
        except AssertionError as exc:
            return exc

    def test_stack_trace_comes_from_the_exception_not_the_handler_state(self, tmp_path):
        """traceback.format_exc() outside an except block recorded 'NoneType: None'."""
        error = self._raised()
        assert sys.exc_info() == (None, None, None)
        path = write_failure_bundle(
            "test_x", error, b"png", ["c"], ["r"], str(tmp_path), page_errors=["p"]
        )
        bundle = json.loads(path.read_text())
        assert "NoneType: None" not in bundle["stackTrace"]
        assert "_raised" in bundle["stackTrace"]
        assert bundle["error"] == "expected 3 items, got 2"
        assert bundle["errorType"] == "AssertionError"
        assert bundle["pageErrors"] == ["p"]

    def test_long_traces_keep_the_end(self):
        error = RuntimeError("x" * (MAX_STACK_CHARS * 2) + "TAIL")
        text = format_stack(error)
        assert text.startswith("...[truncated]") and "TAIL" in text
        assert len(text) <= MAX_STACK_CHARS + 20

    def test_unwritable_directory_returns_none(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("")
        assert write_failure_bundle("t", self._raised(), None, [], [], str(blocker / "sub")) is None
