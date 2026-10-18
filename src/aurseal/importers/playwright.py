"""Playwright JSON reporter importer (``--reporter=json``).

One record per test per project: the spec is the case, ``projectName`` is the
configuration (browser/device matrix never enters the identity). ``results[]`` are the
attempts, numbered by ``retry``.

Status per result: passed -> passed, failed -> failed, timedOut -> failed (reason
timeout), interrupted -> broken, skipped -> skipped. A test expected to fail
(``test.fail()``, ``expectedStatus: failed``) that failed is passed with reason
``expected_failure``. Playwright's own ``flaky`` outcome becomes a final pass with
``flaky_in_run``, derived from the attempts.

The JSON reporter writes one document, so it is read whole; reports are bounded by the
size of the suite, not by test output (attachments are paths, not inline content).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import IO, Any

from aurseal.importers._build import PendingTest, ReportError
from aurseal.model.v1 import Status, TestRecord

_STATUS = {
    "passed": (Status.PASSED, None),
    "failed": (Status.FAILED, None),
    "timedOut": (Status.FAILED, "timeout"),
    "interrupted": (Status.BROKEN, None),
    "skipped": (Status.SKIPPED, None),
}


def _error_message(result: dict[str, Any]) -> str | None:
    error = result.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    errors = result.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        message = errors[0].get("message")
        return str(message) if message else None
    return None


class PlaywrightImporter:
    name = "playwright"

    def sniff(self, head: bytes) -> float:
        text = head.lstrip()
        if not text.startswith(b"{"):
            return 0.0
        if b'"suites"' in text and (b'"config"' in text or b'"specs"' in text):
            return 0.9
        return 0.1

    def parse(self, stream: IO[bytes]) -> Iterator[TestRecord]:
        try:
            document = json.load(stream)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReportError(f"not a Playwright JSON report: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("suites"), list):
            raise ReportError("not a Playwright JSON report: no 'suites' list")
        for suite in document["suites"]:
            path = str(suite.get("file") or suite.get("title") or "")
            yield from self._suite(suite, path, [], top=True)

    def _suite(
        self, suite: dict[str, Any], path: str, levels: list[str], top: bool
    ) -> Iterator[TestRecord]:
        # The file-level suite's title is the file name: it is the path, not a suite level.
        here = levels if top else [*levels, str(suite.get("title") or "")]
        for spec in suite.get("specs") or []:
            yield from self._spec(spec, str(suite.get("file") or path), here)
        for child in suite.get("suites") or []:
            yield from self._suite(child, str(child.get("file") or path), here, top=False)

    def _spec(self, spec: dict[str, Any], path: str, levels: list[str]) -> Iterator[TestRecord]:
        tags = tuple(str(t) for t in spec.get("tags") or [])
        for test in spec.get("tests") or []:
            pending = PendingTest(
                framework="playwright",
                path=path,
                suite=levels,
                title=str(spec.get("title") or ""),
                configuration=str(test.get("projectName") or "") or None,
                tags=tags,
            )
            expected_failure = test.get("expectedStatus") == "failed"
            results = sorted(test.get("results") or [], key=lambda r: int(r.get("retry") or 0))
            for result in results:
                raw = str(result.get("status") or "")
                if raw not in _STATUS:
                    raise ReportError(f"unknown Playwright result status {raw!r}")
                status, reason = _STATUS[raw]
                if expected_failure and status is Status.FAILED:
                    status, reason = Status.PASSED, "expected_failure"
                duration = result.get("duration")
                pending.add(
                    status,
                    max(0, int(duration)) if isinstance(duration, int | float) else None,
                    _error_message(result),
                    reason,
                )
            if not pending.attempts:  # never ran (skipped before start)
                pending.add(Status.SKIPPED)
            yield pending.record()
