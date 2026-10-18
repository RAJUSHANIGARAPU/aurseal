"""JUnit XML import: the zero-effort way for any framework, in any language, to push results.

``testsuite``/``testcase`` become run-bundle records:

* outcome — ``<failure>`` failed, ``<error>`` error, ``<skipped>`` skipped (pytest's
  ``type="pytest.xfail"`` skip is xfailed), otherwise passed;
* ``time`` — ``duration_ms``;
* test id — for pytest-produced XML the node id is rebuilt (``tests.test_x.TestA`` +
  ``test_b[1]`` -> ``tests/test_x.py::TestA::test_b[1]``, or the ``file`` attribute when
  present); for any other producer it is ``<classname>::<name>``;
* the failure's ``message`` attribute (or first text line) is the message; its text plus
  ``system-out``/``system-err`` form the long report (truncated by the store);
* ``[[ATTACHMENT|path]]`` lines become evidence links;
* ``<property>`` elements carry links and identity: ``requirement``, ``feature``,
  ``defect``, ``owner``, ``priority`` and ``case_id`` (a pin).

XML safety: a document with a DOCTYPE or ENTITY declaration is rejected before parsing
(no entity expansion, no external entities). When ``defusedxml`` is installed it is used
as well.

Identity on import is weak by nature: JUnit carries no stable ids, so cases rely on
classname plus name. Without ``--run-id`` the run id is derived from the file's content,
so importing the same file twice changes nothing.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from results._util import to_utc
from results.bundle import BundleRecord, ImportReport, import_records, validate_record
from results.catalogue import case_id_for_key, case_key_from_nodeid
from results.models import (
    CaseRecord,
    EvidenceRecord,
    InvocationRecord,
    ResultRecord,
    RunRecord,
)

_UNSAFE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_ATTACHMENT = re.compile(r"\[\[ATTACHMENT\|([^\]]+)\]\]")
_EVIDENCE_KIND = {
    ".png": "screenshot",
    ".jpg": "screenshot",
    ".jpeg": "screenshot",
    ".gif": "screenshot",
    ".zip": "trace",
    ".webm": "video",
    ".mp4": "video",
    ".har": "har",
    ".log": "log",
    ".txt": "log",
}
_LINK_PROPS = {
    "requirement": "requirement",
    "requirements": "requirement",
    "feature": "feature",
    "defect": "defect",
    "known_defect": "defect",
}


class JUnitError(ValueError):
    """The document is not acceptable JUnit XML (unsafe, unreadable or not JUnit)."""


@dataclass
class JUnitCase:
    suite: str
    classname: str
    name: str
    outcome: str
    duration_ms: int | None = None
    message: str | None = None
    failure_type: str | None = None
    text: str | None = None
    system_out: str | None = None
    system_err: str | None = None
    file: str | None = None
    properties: list[tuple[str, str]] = field(default_factory=list)
    timestamp: str | None = None


def _load(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def parse_xml(data: bytes) -> ET.Element:
    """Parse untrusted XML: DOCTYPE/ENTITY refused; defusedxml used when installed."""
    if _UNSAFE.search(data):
        raise JUnitError("DOCTYPE or ENTITY declarations are not accepted")
    try:
        import defusedxml.ElementTree as safe_et  # optional

        return safe_et.fromstring(data)
    except ImportError:
        pass
    except Exception as exc:  # defusedxml's own refusals
        raise JUnitError(f"unsafe or unreadable XML: {exc}") from exc
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise JUnitError(f"unreadable XML: {exc}") from exc


def _seconds_to_ms(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(int(round(float(value.replace(",", "")) * 1000)), 0)
    except ValueError:
        return None


def _props(element: ET.Element) -> list[tuple[str, str]]:
    out = []
    for props in element.findall("properties"):
        for prop in props.findall("property"):
            name, value = prop.get("name"), prop.get("value")
            if value is None:
                value = (prop.text or "").strip()
            if name:
                out.append((name, value or ""))
    return out


def iter_testcases(source: str | Path | bytes) -> Iterator[JUnitCase]:
    """Every ``testcase`` of a JUnit document, with its suite's context."""
    root = parse_xml(_load(source))
    if root.tag not in ("testsuites", "testsuite"):
        raise JUnitError(f"root element <{root.tag}> is not <testsuites> or <testsuite>")
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        suite_props = _props(suite)
        for tc in suite.findall("testcase"):
            outcome, message, ftype, text = "passed", None, None, None
            for tag in ("failure", "error", "skipped"):
                child = tc.find(tag)
                if child is None:
                    continue
                message = child.get("message")
                ftype = child.get("type")
                text = (child.text or "").strip() or None
                if tag == "skipped":
                    outcome = "xfailed" if (ftype or "").endswith("xfail") else "skipped"
                else:
                    outcome = "failed" if tag == "failure" else "error"
                if not message and text:
                    message = text.splitlines()[0]
                break
            out_el, err_el = tc.find("system-out"), tc.find("system-err")
            yield JUnitCase(
                suite=suite.get("name") or "",
                classname=tc.get("classname") or "",
                name=tc.get("name") or "",
                outcome=outcome,
                duration_ms=_seconds_to_ms(tc.get("time")),
                message=message,
                failure_type=ftype,
                text=text,
                system_out=(out_el.text or "").strip() or None if out_el is not None else None,
                system_err=(err_el.text or "").strip() or None if err_el is not None else None,
                file=tc.get("file"),
                properties=[*suite_props, *_props(tc)],
                timestamp=suite.get("timestamp"),
            )


def pytest_nodeid(classname: str, name: str, file: str | None = None) -> str:
    """Rebuild a pytest node id from JUnit ``classname`` + ``name``."""
    parts = [p for p in classname.split(".") if p]
    classes: list[str] = []
    while parts and parts[-1][:1].isupper():
        classes.insert(0, parts.pop())
    path = file.replace("\\", "/") if file else ("/".join(parts) + ".py" if parts else "")
    return "::".join(x for x in (path, *classes, name) if x)


def _nodeid(case: JUnitCase, framework: str) -> str:
    if framework == "pytest":
        return pytest_nodeid(case.classname, case.name, case.file)
    return f"{case.classname}::{case.name}" if case.classname else case.name


def _fingerprint(case: JUnitCase, nodeid: str) -> str | None:
    if case.outcome not in ("failed", "error"):
        return None
    basis = f"{case.failure_type or ''}|{nodeid}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def _long(case: JUnitCase) -> str | None:
    parts = []
    if case.text:
        parts.append(case.text)
    if case.system_out:
        parts.append("----- system-out -----\n" + case.system_out)
    if case.system_err:
        parts.append("----- system-err -----\n" + case.system_err)
    return "\n".join(parts) or None


def _evidence_kind(path: str) -> str:
    return _EVIDENCE_KIND.get(Path(path).suffix.lower(), "other")


def parse_junit(
    path_or_bytes: str | Path | bytes,
    *,
    framework: str = "junit",
    run_id: str | None = None,
    rejected: list[dict[str, Any]] | None = None,
) -> Iterator[BundleRecord]:
    """Run-bundle records for a JUnit document (run, invocation, cases, results, ...)."""
    data = _load(path_or_bytes)
    cases_in = list(iter_testcases(data))
    if framework == "junit" and any(c.suite == "pytest" for c in cases_in):
        framework = "pytest"
    rid = run_id or "junit-" + hashlib.sha1(data).hexdigest()[:16]
    started = to_utc(next((c.timestamp for c in cases_in if c.timestamp), None))
    yield BundleRecord(
        "run",
        RunRecord(
            run_id=rid,
            source="junit-import",
            framework=framework,
            name=next((c.suite for c in cases_in if c.suite), None),
            started_at=started,
            expected_count=len(cases_in),
        ).to_json(),
    )
    yield BundleRecord(
        "invocation",
        InvocationRecord(run_id=rid, seq=1, started_at=started).to_json(),
    )
    seen_cases: dict[str, CaseRecord] = {}
    results: list[ResultRecord] = []
    evidence: list[EvidenceRecord] = []
    seen_nodes: set[str] = set()
    for case in cases_in:
        if not case.name:
            if rejected is not None:
                rejected.append({"classname": case.classname, "reason": "testcase without a name"})
            continue
        nodeid = _nodeid(case, framework)
        if nodeid in seen_nodes:
            if rejected is not None:
                rejected.append({"nodeid": nodeid, "reason": "duplicate testcase in the file"})
            continue
        seen_nodes.add(nodeid)
        props = case.properties
        links = tuple(
            dict.fromkeys((_LINK_PROPS[k], v) for k, v in props if k in _LINK_PROPS and v)
        )
        pin = next((v for k, v in props if k == "case_id" and v), None)
        key = case_key_from_nodeid(nodeid)
        case_id = pin or case_id_for_key(key)
        if case_id not in seen_cases:
            seen_cases[case_id] = CaseRecord(
                case_id=case_id,
                case_key=key,
                framework=framework,
                title=case.name,
                module=case.classname or None,
                file_path=case.file,
                owner=next((v for k, v in props if k == "owner" and v), None),
                priority=next((v for k, v in props if k == "priority" and v), None),
                links=links,
            )
        results.append(
            ResultRecord(
                run_id=rid,
                nodeid=nodeid,
                outcome=case.outcome,
                attempt=1,
                invocation=1,
                case_id=case_id,
                param_id=nodeid[nodeid.index("[") + 1 : -1]
                if nodeid.endswith("]") and "[" in nodeid
                else None,
                duration_ms=case.duration_ms,
                message=case.message,
                longrepr=_long(case),
                failure_fingerprint=_fingerprint(case, nodeid),
                skip_reason=case.message if case.outcome == "skipped" else None,
                defect_ref=next((v for k, v in props if k in ("defect", "known_defect")), None),
            )
        )
        for blob in (case.system_out, case.system_err, case.text):
            for path in _ATTACHMENT.findall(blob or ""):
                evidence.append(
                    EvidenceRecord(
                        run_id=rid,
                        kind=_evidence_kind(path.strip()),
                        uri=path.strip(),
                        nodeid=nodeid,
                        attempt=1,
                    )
                )
    for c in seen_cases.values():
        yield BundleRecord("case", c.to_json())
    for r in results:
        yield BundleRecord("result", r.to_json())
    for e in evidence:
        yield BundleRecord("evidence", e.to_json())
    yield BundleRecord(
        "run_end",
        {"run_id": rid, "status": "finished", "finished_at": None, "integrity_verdict": None},
    )


def import_junit(store: Any, path: str | Path | bytes, **kw: Any) -> ImportReport:
    """Validate and import a JUnit document; rejected test cases are listed, not dropped."""
    rejected: list[dict[str, Any]] = []
    records = list(parse_junit(path, rejected=rejected, **kw))
    for number, rec in enumerate(records, 1):
        validate_record({"type": rec.type, "schema_version": 1, **rec.data}, number)
    report = import_records(store, records)
    report.rejected.extend(rejected)
    return report


__all__ = [
    "JUnitCase",
    "JUnitError",
    "import_junit",
    "iter_testcases",
    "parse_junit",
    "parse_xml",
    "pytest_nodeid",
]
