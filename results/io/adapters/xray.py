"""Xray-style formatters: the Xray JSON import format and a JUnit flavour with test keys.

* Results JSON: ``{"info": {...}, "tests": [{"testKey", "start", "finish", "status",
  "comment", "evidence"}]}``; status PASSED/FAILED, skipped -> TODO, not run -> ABORTED.
  Evidence: local files up to 1 MB are embedded (base64, as Xray expects); anything else
  is listed as a link in the comment.
* JUnit flavour: one ``<testcase>`` per result with ``<property name="test_key">``, for
  Xray's JUnit endpoint.
* Cases CSV: Summary, Description, Test Type, Steps, Labels.

Results without a ``tms:xray`` link are reported as unmapped.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import os
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from results.io.adapters.base import FormatterOnly, MappedResults

STATUS = {
    "passed": "PASSED",
    "failed": "FAILED",
    "error": "FAILED",
    "xfailed": "FAILED",
    "skipped": "TODO",
    "not_run": "ABORTED",
}
EMBED_LIMIT = 1024 * 1024


class XrayAdapter(FormatterOnly):
    name = "xray"
    link_kind = "tms:xray"

    def __init__(self, transport=None, *, artifact_root: Path | None = None) -> None:
        super().__init__(transport)
        root = artifact_root or Path(os.environ.get("RESULTS_ARTIFACT_ROOT") or Path.cwd())
        self.artifact_root = Path(root)

    def format_cases(self, cases: Sequence[Any]) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Summary", "Description", "Test Type", "Steps", "Labels"])
        for c in cases:
            labels = " ".join(m.get("name", "") for m in c.markers)
            writer.writerow(
                [
                    c.title or c.case_key,
                    c.description or "",
                    "Automated",
                    "\n".join(f"{i}. {s}" for i, s in enumerate(c.steps, 1)),
                    labels,
                ]
            )
        return buffer.getvalue().encode("utf-8")

    def _evidence(self, rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
        embedded, linked = [], []
        for ev in rows:
            uri = ev["uri"]
            path = Path(uri) if Path(uri).is_absolute() else self.artifact_root / uri
            if not uri.startswith(("http://", "https://")) and path.is_file():
                if path.stat().st_size <= EMBED_LIMIT:
                    embedded.append(
                        {
                            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                            "filename": path.name,
                            "contentType": mimetypes.guess_type(path.name)[0]
                            or "application/octet-stream",
                        }
                    )
                    continue
            linked.append(f"{ev['kind']}: {uri}")
        return embedded, linked

    def map_results(
        self,
        run: Any,
        results: Sequence[dict[str, Any]],
        mapping: dict[str, str],
        evidence: dict[str, list[dict[str, Any]]] | None = None,
    ) -> MappedResults:
        tests, unmapped = [], []
        for r in results:
            key = mapping.get(r.get("case_id") or "")
            if not key:
                unmapped.append(r["nodeid"])
                continue
            embedded, linked = self._evidence((evidence or {}).get(r["nodeid"], []))
            comment = r.get("message") or ""
            if r["outcome"] == "xfailed":
                comment = f"Expected failure (known defect) reproduced. {comment}".strip()
            if r["outcome"] == "skipped" and r.get("skip_reason"):
                comment = f"Skipped: {r['skip_reason']}"
            if linked:
                comment = (comment + "\nEvidence: " + "; ".join(linked)).strip()
            item: dict[str, Any] = {"testKey": key, "status": STATUS[r["outcome"]]}
            if r.get("started_at"):
                item["start"] = r["started_at"]
            if r.get("finished_at"):
                item["finish"] = r["finished_at"]
            if comment:
                item["comment"] = comment
            if embedded:
                item["evidence"] = embedded
            tests.append(item)
        info = {
            "summary": f"Automated run {run.run_id}",
            "description": f"Imported from the results store (source: {run.source}).",
        }
        if run.started_at:
            info["startDate"] = run.started_at
        if run.finished_at:
            info["finishDate"] = run.finished_at
        if run.git_sha:
            info["revision"] = run.git_sha
        if run.environment:
            info["testEnvironments"] = [run.environment]
        mapped = MappedResults({"info": info, "tests": tests}, unmapped, [])
        self.last = mapped
        return mapped

    def format_results(
        self,
        run: Any,
        results: Sequence[dict[str, Any]],
        mapping: dict[str, str],
        evidence: dict[str, list[dict[str, Any]]] | None = None,
    ) -> bytes:
        mapped = self.map_results(run, results, mapping, evidence)
        return json.dumps(mapped.payload, indent=2, ensure_ascii=False).encode("utf-8")

    def format_junit(
        self, run: Any, results: Sequence[dict[str, Any]], mapping: dict[str, str]
    ) -> bytes:
        """JUnit XML whose test cases carry ``test_key`` properties (Xray's JUnit import)."""
        suite = ET.Element("testsuite", name=str(run.run_id), tests=str(len(results)))
        unmapped = []
        for r in results:
            classname, _, name = r["nodeid"].rpartition("::")
            tc = ET.SubElement(suite, "testcase", classname=classname, name=name or r["nodeid"])
            if r.get("duration_ms") is not None:
                tc.set("time", f"{r['duration_ms'] / 1000:.3f}")
            key = mapping.get(r.get("case_id") or "")
            if key:
                props = ET.SubElement(tc, "properties")
                ET.SubElement(props, "property", name="test_key", value=key)
            else:
                unmapped.append(r["nodeid"])
            if r["outcome"] in ("failed", "xfailed"):
                ET.SubElement(tc, "failure", message=r.get("message") or "")
            elif r["outcome"] == "error":
                ET.SubElement(tc, "error", message=r.get("message") or "")
            elif r["outcome"] in ("skipped", "not_run"):
                ET.SubElement(tc, "skipped", message=r.get("skip_reason") or r["outcome"])
        self.last = MappedResults({}, unmapped, [])
        ET.indent(suite)
        return ET.tostring(suite, encoding="utf-8", xml_declaration=True)


__all__ = ["XrayAdapter"]
