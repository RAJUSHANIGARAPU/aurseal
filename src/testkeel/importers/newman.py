"""Newman importer: the ``json`` reporter's run summary (``--reporters json``).

The file is Newman's run summary (``collection``, ``environment``, ``run``); its
``run.executions`` holds one entry per request sent, with the ``item`` it ran, the
``response``, ``assertions`` (``{assertion, skipped, error}``), and ``requestError`` or
``prerequestScript``/``testScript`` error lists when something broke (Newman's
``lib/run/summary.js``).

One record per request (collection item) and iteration:

* path = the collection name, suite = the folders above the request, name = its name;
* the environment name is the configuration;
* with more than one iteration (a data file), the iteration is the variant
  (``iteration-2``): the same request, different data;
* a request that ran more than once in one iteration (``setNextRequest`` loops) records
  each run as an attempt.

Status: ``requestError`` or a script error -> broken; a failed assertion -> failed; every
assertion skipped -> skipped; otherwise passed. Duration is ``response.responseTime``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import IO, Any

from testkeel.importers._build import PendingTest, ReportError
from testkeel.model.v1 import Status, TestRecord

FRAMEWORK = "newman"
_SCRIPT_ERRORS = ("prerequestScript", "testScript")


def _folders(items: list[Any], levels: list[str], out: dict[str, list[str]]) -> None:
    """Map every request id to the folder names above it."""
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("item"), list):  # a folder
            _folders(item["item"], [*levels, str(item.get("name") or "")], out)
        elif item.get("id"):
            out[str(item["id"])] = levels


def _error_text(error: object) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error.get("name") or "error")
    return str(error)


def _outcome(execution: dict[str, Any]) -> tuple[Status, str | None]:
    if execution.get("requestError"):
        return Status.BROKEN, _error_text(execution["requestError"])
    for key in _SCRIPT_ERRORS:
        for entry in execution.get(key) or []:
            if isinstance(entry, dict) and entry.get("error"):
                return Status.BROKEN, f"{key}: {_error_text(entry['error'])}"
    assertions = [a for a in execution.get("assertions") or [] if isinstance(a, dict)]
    for assertion in assertions:
        if assertion.get("error") and not assertion.get("skipped"):
            name = assertion.get("assertion") or "assertion"
            return Status.FAILED, f"{name}: {_error_text(assertion['error'])}"
    if assertions and all(a.get("skipped") for a in assertions):
        return Status.SKIPPED, None
    return Status.PASSED, None


class NewmanImporter:
    name = "newman"

    def sniff(self, head: bytes) -> float:
        text = head.lstrip()
        if not text.startswith(b"{") or b'"collection"' not in text:
            return 0.0
        if b"getpostman.com" in text or b'"_postman_id"' in text or b'"executions"' in text:
            return 0.85
        return 0.1

    def parse(self, stream: IO[bytes]) -> Iterator[TestRecord]:
        try:
            document = json.load(stream)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReportError(f"not a Newman JSON report: {exc}") from exc
        run = document.get("run") if isinstance(document, dict) else None
        if not isinstance(run, dict) or not isinstance(run.get("executions"), list):
            raise ReportError("not a Newman JSON report: no 'run.executions' list")
        collection = document.get("collection") or {}
        info = collection.get("info") or {}
        path = str(info.get("name") or collection.get("name") or "")
        folders: dict[str, list[str]] = {}
        _folders(collection.get("item") or [], [], folders)
        environment = document.get("environment") or {}
        configuration = str(environment.get("name") or "") or None
        executions = [e for e in run["executions"] if isinstance(e, dict)]
        iterations = {int((e.get("cursor") or {}).get("iteration") or 0) for e in executions}
        many = len(iterations) > 1 or any(
            int((e.get("cursor") or {}).get("cycles") or 1) > 1 for e in executions
        )
        tests: dict[tuple[str, int], PendingTest] = {}
        for execution in executions:
            item = execution.get("item") or {}
            item_id = str(item.get("id") or execution.get("id") or "")
            name = str(item.get("name") or "")
            if not name:
                raise ReportError("a Newman execution has no item name")
            iteration = int((execution.get("cursor") or {}).get("iteration") or 0)
            key = (item_id or name, iteration)
            pending = tests.get(key)
            if pending is None:
                pending = tests[key] = PendingTest(
                    framework=FRAMEWORK,
                    path=path,
                    suite=folders.get(item_id, []),
                    title=name,
                    variant=f"iteration-{iteration + 1}" if many else None,
                    configuration=configuration,
                )
            status, message = _outcome(execution)
            response = execution.get("response") or {}
            elapsed = response.get("responseTime")
            duration = max(0, int(elapsed)) if isinstance(elapsed, int | float) else None
            pending.add(status, duration, message)
        for pending in tests.values():
            yield pending.record()
