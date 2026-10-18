"""Cypress importer: mochawesome JSON (one file or a ``mochawesome-merge`` report) and the
results object of the Cypress Module API (``cypress.run()``, written with ``JSON.stringify``).

**Module API** (``CypressRunResult`` in Cypress's ``cli/types/cypress-npm-api.d.ts``):
``runs[].spec.relative`` is the path, ``runs[].tests[].title`` the suite levels plus the
test title, ``tests[].attempts[].state`` the attempts (retries) and ``browserName`` the
configuration. The last attempt carries ``displayError`` and the test's ``duration``.
A ``{"status": "failed", "message": ...}`` result (Cypress could not run) is refused.

**mochawesome** (``results[]`` of root suites, each with ``file``, ``tests`` and nested
``suites``): one record per test; ``err.message`` is the message. mochawesome keeps only
the final attempt, plus ``attempts`` (the retry count, mochawesome 7.1 and later). Mocha
retries a test only after it failed, so the attempts before the last are failed attempts
with no detail of their own. A failed hook (``beforeHooks``/``afterHooks``) is a broken
record named after the hook; the tests it stopped are skipped with reason ``not_run``.

State mapping (both formats): passed -> passed, failed -> failed (``timedOut`` adds reason
``timeout``), pending -> skipped, skipped / never run -> skipped (reason ``not_run``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import IO, Any

from aurseal.importers._build import PendingTest, ReportError
from aurseal.model.v1 import Status, TestRecord

FRAMEWORK = "cypress"
_STATE: dict[str, tuple[Status, str | None]] = {
    "passed": (Status.PASSED, None),
    "failed": (Status.FAILED, None),
    "pending": (Status.SKIPPED, None),
    "skipped": (Status.SKIPPED, "not_run"),
}
_Key = tuple[str, str, str, str | None]


def _state(raw: object) -> tuple[Status, str | None]:
    if raw is None:  # mochawesome: a test that never ran
        return Status.SKIPPED, "not_run"
    if str(raw) not in _STATE:
        raise ReportError(f"unknown Cypress test state {raw!r}")
    return _STATE[str(raw)]


def _ms(value: object) -> int | None:
    return max(0, round(value)) if isinstance(value, int | float) else None


def _err_message(err: object) -> str | None:
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    return None


class CypressImporter:
    name = "cypress"

    def sniff(self, head: bytes) -> float:
        text = head.lstrip()
        if not text.startswith(b"{"):
            return 0.0
        if b'"cypressVersion"' in text or (b'"runs"' in text and b'"totalTests"' in text):
            return 0.9
        if b'"results"' in text and (
            b'"passPercent"' in text or b'"fullFile"' in text or b'"mochawesome"' in text
        ):
            return 0.9
        return 0.0

    def parse(self, stream: IO[bytes]) -> Iterator[TestRecord]:
        try:
            document = json.load(stream)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReportError(f"not a Cypress report: {exc}") from exc
        if not isinstance(document, dict):
            raise ReportError("not a Cypress report: not a JSON object")
        if document.get("status") == "failed" and "runs" not in document:
            raise ReportError(f"Cypress did not run: {document.get('message') or 'no message'}")
        tests: dict[_Key, PendingTest] = {}
        if isinstance(document.get("runs"), list):
            self._module_api(document, tests)
        elif isinstance(document.get("results"), list):
            for root in document["results"]:
                if isinstance(root, dict):
                    path = str(root.get("file") or root.get("fullFile") or "")
                    self._suite(root, path, [], tests)
        else:
            raise ReportError("not a Cypress report: no 'runs' (Module API) or 'results' list")
        for pending in tests.values():
            yield pending.record()

    @staticmethod
    def _pending(
        tests: dict[_Key, PendingTest],
        path: str,
        levels: list[str],
        title: str,
        configuration: str | None,
    ) -> PendingTest:
        key = (path, "\x1f".join(levels), title, configuration)
        pending = tests.get(key)
        if pending is None:
            pending = tests[key] = PendingTest(
                framework=FRAMEWORK,
                path=path,
                suite=levels,
                title=title,
                configuration=configuration,
            )
        return pending

    def _module_api(self, document: dict[str, Any], tests: dict[_Key, PendingTest]) -> None:
        configuration = str(document.get("browserName") or "") or None
        for run in document["runs"]:
            spec = run.get("spec") or {}
            path = str(spec.get("relative") or spec.get("name") or "")
            for test in run.get("tests") or []:
                titles = [str(t) for t in test.get("title") or []]
                if not titles:
                    raise ReportError(f"a test in {path!r} has no title")
                pending = self._pending(tests, path, titles[:-1], titles[-1], configuration)
                attempts = [a for a in test.get("attempts") or [] if isinstance(a, dict)]
                if not attempts:
                    attempts = [{"state": test.get("state")}]
                for index, attempt in enumerate(attempts):
                    last = index == len(attempts) - 1
                    status, reason = _state(attempt.get("state"))
                    duration = _ms(attempt.get("wallClockDuration") or attempt.get("duration"))
                    if last and duration is None:
                        duration = _ms(test.get("duration"))
                    message = _err_message(attempt.get("error"))
                    if last and test.get("displayError"):
                        message = str(test["displayError"])
                    pending.add(status, duration, message, reason)

    def _suite(
        self,
        suite: dict[str, Any],
        path: str,
        levels: list[str],
        tests: dict[_Key, PendingTest],
    ) -> None:
        for hook in [*(suite.get("beforeHooks") or []), *(suite.get("afterHooks") or [])]:
            if isinstance(hook, dict) and hook.get("state") == "failed":
                self._hook(hook, path, levels, tests)
        for test in suite.get("tests") or []:
            if test.get("isHook"):
                if test.get("state") == "failed":
                    self._hook(test, path, levels, tests)
                continue
            pending = self._pending(tests, path, levels, str(test.get("title") or ""), None)
            # A pending test has no state; ``pending: true`` says what it is.
            status, reason = _state("pending" if test.get("pending") else test.get("state"))
            if status is Status.FAILED and test.get("timedOut"):
                reason = "timeout"
            retries = int(test.get("attempts") or 1) - 1
            for _ in range(max(0, retries)):  # mocha retries only a failed attempt
                pending.add(Status.FAILED)
            pending.add(status, _ms(test.get("duration")), _err_message(test.get("err")), reason)
        for child in suite.get("suites") or []:
            if isinstance(child, dict):
                child_path = str(child.get("file") or path)
                self._suite(child, child_path, [*levels, str(child.get("title") or "")], tests)

    def _hook(
        self,
        hook: dict[str, Any],
        path: str,
        levels: list[str],
        tests: dict[_Key, PendingTest],
    ) -> None:
        pending = self._pending(tests, path, levels, str(hook.get("title") or "hook"), None)
        pending.add(Status.BROKEN, _ms(hook.get("duration")), _err_message(hook.get("err")))
