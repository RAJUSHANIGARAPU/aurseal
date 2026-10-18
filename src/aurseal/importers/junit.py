"""JUnit XML importer (pytest ``--junitxml``, Maven Surefire, and compatible reporters).

The XML is read with ``iterparse`` and every ``<testcase>`` element is released once read,
so memory grows with the number of distinct tests, not with the size of the file (failure
output and system-out are the bulk of a large report).

Status: ``<failure>`` -> failed, ``<error>`` -> broken, ``<skipped>`` -> skipped (an
``xfail`` skip keeps ``reason=xfail``), none -> passed. Repeated ``<testcase>`` elements
with the same identity are attempts of one test (rerun plugins), as are Surefire's
``<flakyFailure>``/``<rerunFailure>`` children.

pytest writes ``classname="tests.api.test_refund.TestRefund"`` without a ``file``
attribute; the module path is rebuilt from the last dotted part that looks like a test
module (``test_*`` / ``*_test``). Anything else keeps the class name as its path.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import IO

from aurseal.importers._build import PendingTest, ReportError, seconds_to_ms
from aurseal.model.identity import split_variant
from aurseal.model.v1 import Status, TestRecord

_HEAD_RE = re.compile(rb"<testsuites?[\s>]")
_PHRASED_ONLY_RE = re.compile(r"^\[\d+\]")  # Surefire phrased name without the method
_CASE_PROPERTIES = ("case_id", "case", "test_id")
_RERUN_TAGS = {
    "flakyFailure": Status.FAILED,
    "rerunFailure": Status.FAILED,
    "flakyError": Status.BROKEN,
    "rerunError": Status.BROKEN,
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _split_classname(classname: str, file: str | None) -> tuple[str, list[str], bool]:
    """(path, suite levels, looks like pytest) from a JUnit classname."""
    parts = [p for p in classname.split(".") if p]
    if file:
        module_parts = [p for p in re.split(r"[/\\]", file) if p]
        stem = module_parts[-1].rsplit(".", 1)[0] if module_parts else ""
        suite = parts[parts.index(stem) + 1 :] if stem in parts else []
        return file, suite, file.endswith(".py")
    for index in range(len(parts) - 1, -1, -1):
        part = parts[index]
        if part.startswith("test_") or part.endswith("_test"):
            return "/".join(parts[: index + 1]) + ".py", parts[index + 1 :], True
    return classname, [], False


class JUnitImporter:
    name = "junit"

    def sniff(self, head: bytes) -> float:
        if _HEAD_RE.search(head):
            return 0.9
        if head.lstrip().startswith(b"<?xml"):
            return 0.2
        return 0.0

    def parse(self, stream: IO[bytes]) -> Iterator[TestRecord]:
        tests: dict[tuple[str, str, str, str, str | None], PendingTest] = {}
        suite_names: list[str] = []
        try:
            for event, element in ET.iterparse(stream, events=("start", "end")):
                tag = _local(element.tag)
                if tag == "testsuite":
                    if event == "start":
                        suite_names.append(element.get("name") or "")
                    else:
                        suite_names.pop()
                        element.clear()
                elif tag == "testcase" and event == "end":
                    self._case(element, suite_names, tests)
                    element.clear()
        except ET.ParseError as exc:
            raise ReportError(f"not well-formed JUnit XML: {exc}") from exc
        for pending in tests.values():
            yield pending.record()

    def _case(
        self,
        element: ET.Element,
        suite_names: list[str],
        tests: dict[tuple[str, str, str, str, str | None], PendingTest],
    ) -> None:
        raw_name = element.get("name") or ""
        if not raw_name or _PHRASED_ONLY_RE.match(raw_name):
            raise ReportError(
                f"test case name {raw_name!r} has no method name; configure the reporter "
                "to include it (Surefire: phrased names need 3.0.0-M6 or later)"
            )
        classname = element.get("classname") or ""
        path, suite, pythonic = _split_classname(classname, element.get("file"))
        framework = "pytest" if pythonic or "pytest" in suite_names else "junit"
        title, variant = split_variant(raw_name)
        properties = {
            (p.get("name") or ""): (p.get("value") or "") for p in element.iter("property")
        }
        case_id = next((properties[k] for k in _CASE_PROPERTIES if properties.get(k)), None)
        key = (framework, path, ".".join(suite), title, variant)
        pending = tests.get(key)
        if pending is None:
            pending = tests[key] = PendingTest(
                framework=framework,
                path=path,
                suite=suite,
                title=title,
                variant=variant,
                case_id=case_id,
            )
        children = {_local(c.tag): c for c in element}
        for child in element:
            rerun = _RERUN_TAGS.get(_local(child.tag))
            if rerun is not None:
                pending.add(rerun, message=child.get("message"))
        duration = seconds_to_ms(element.get("time"))
        if "failure" in children:
            pending.add(Status.FAILED, duration, children["failure"].get("message"))
        elif "error" in children:
            pending.add(Status.BROKEN, duration, children["error"].get("message"))
        elif "skipped" in children:
            skipped = children["skipped"]
            marker = f"{skipped.get('type') or ''} {skipped.get('message') or ''}".lower()
            reason = "xfail" if "xfail" in marker else None
            pending.add(Status.SKIPPED, duration, skipped.get("message"), reason)
        else:
            pending.add(Status.PASSED, duration)
