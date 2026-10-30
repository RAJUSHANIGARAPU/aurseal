"""
Keep the trace and the video of a test only when someone will look at them.

The root ``conftest.py`` saves a trace and keeps a video for every test. A
snapshot trace of a long browser test runs to hundreds of megabytes and takes
real time to serialise; nobody opens the one from a passing test. The policy here:

* **failed** (any phase) -> keep the trace and the video;
* **skipped during the call** -> keep them too. A test that decided at run time it
  could not judge (``pytest.skip`` from inside the test) is exactly the case where
  the screen it was looking at matters;
* **passed** -> stop the trace *without a path*, which discards it without
  serialising, and delete the video file once the context has flushed it;
* ``TRACE_ALWAYS=true`` / ``VIDEO_ALWAYS=true`` keep everything, for an evidence run.

Teardown needs to know the outcome before the fixture finishes, which pytest does
not hand to fixtures. ``OutcomeRecorder`` is a small ``pytest_runtest_makereport``
hook that stores each phase's report on the item, so ``run_failed(request.node)``
answers it in fixture teardown. Register it once (see the integration notes).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.artifact_redaction import cookie_values, redact_trace
from core.logger import logger

_REPORTS = pytest.StashKey[dict]()
PLUGIN_NAME = "artifact-outcome-recorder"


def _flag(environ: Mapping[str, str], name: str) -> bool:
    return environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RetentionPolicy:
    trace_always: bool = False
    video_always: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RetentionPolicy:
        environ = os.environ if environ is None else environ
        return cls(_flag(environ, "TRACE_ALWAYS"), _flag(environ, "VIDEO_ALWAYS"))

    def keep_trace(self, worth_keeping: bool) -> bool:
        return worth_keeping or self.trace_always

    def keep_video(self, worth_keeping: bool) -> bool:
        return worth_keeping or self.video_always


# ── outcome ──────────────────────────────────────────────────────────────────


class OutcomeRecorder:
    """Stores every phase's report on the item for fixture teardown to read."""

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        item.stash.setdefault(_REPORTS, {})[report.when] = report


def register(config: pytest.Config) -> None:
    if not config.pluginmanager.hasplugin(PLUGIN_NAME):
        config.pluginmanager.register(OutcomeRecorder(), PLUGIN_NAME)


def phase_reports(item) -> dict:
    return item.stash.get(_REPORTS, {})


def run_failed(item) -> bool:
    """True when setup or the call failed (teardown has not reported yet)."""
    return any(report.failed for report in phase_reports(item).values())


def worth_keeping(item) -> bool:
    """
    Failed, or skipped from inside the test body. An expected failure (``xfail``,
    ``known_defect``) is pytest's "skipped" too, but it is the red everyone already
    knows about, so it does not keep artifacts.
    """
    call = phase_reports(item).get("call")
    abstained = call is not None and call.skipped and not hasattr(call, "wasxfail")
    return run_failed(item) or bool(abstained)


# ── trace and video ──────────────────────────────────────────────────────────


def finish_trace(
    context, keep: bool, path: str | Path, known_values: Iterable[str] = ()
) -> Path | None:
    """
    Stop tracing; write ``path`` only when ``keep``. Returns the path written, if any.

    A kept trace is rewritten with session credentials masked. The context's cookie
    values are read before the trace is written, and ``known_values`` (for example
    cookies read when tracing started) are masked as well. If the rewrite fails the
    trace is deleted and nothing is returned.
    """
    try:
        if keep:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            values = [*known_values, *cookie_values(context)]
            context.tracing.stop(path=str(path))
            return Path(path) if redact_trace(path, values) else None
        context.tracing.stop()
    except Exception as exc:  # noqa: BLE001 - evidence never fails a test
        logger.warning("stopping the trace failed: %s", exc)
    return None


def page_videos(context) -> list:
    """Video handles of every page; read them *before* the context closes."""
    videos = []
    for page in list(context.pages):
        try:
            if page.video:
                videos.append(page.video)
        except Exception as exc:  # noqa: BLE001
            logger.warning("video handle unavailable: %s", exc)
    return videos


def finalize_videos(videos: list, keep: bool, destinations: list[str | Path]) -> list[Path]:
    """
    After the context closed (which flushes the files): move kept videos to
    ``destinations``, delete the rest. Returns the kept paths.
    """
    kept: list[Path] = []
    for video, destination in zip(videos, destinations, strict=False):
        try:
            source = Path(video.path())
        except Exception as exc:  # noqa: BLE001
            logger.warning("video path unavailable: %s", exc)
            continue
        if not source.exists():
            continue
        if keep:
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            kept.append(Path(destination))
        else:
            try:
                source.unlink()
            except OSError as exc:
                # Costs disk, not correctness; never turn a green test red over it.
                logger.debug("could not delete a passing test's video %s: %s", source, exc)
    return kept
