"""TestRail-style formatters: a cases CSV for the CSV importer, results for add_results_for_cases.

* Cases CSV columns: Title, Section, Priority, Type, Preconditions, Steps, Expected Result,
  References (requirement links).
* Results JSON: ``{"results": [{"case_id": int, "status_id": int, "comment", "elapsed",
  "version", "defects"}]}`` with status ids passed=1, failed=5, retest=4. Skipped and
  not-run results are *omitted* (TestRail cannot set "untested" through results) and
  listed as such; results without a ``tms:testrail`` link are listed as unmapped.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Sequence
from typing import Any

from results.io.adapters.base import FormatterOnly, MappedResults

STATUS = {"passed": 1, "failed": 5, "error": 5, "xfailed": 5}
CASE_COLUMNS = (
    "Title",
    "Section",
    "Priority",
    "Type",
    "Preconditions",
    "Steps",
    "Expected Result",
    "References",
)


def _elapsed(ms: int | None) -> str | None:
    if not ms:
        return None
    seconds = max(1, round(ms / 1000))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m {sec}s" if minutes else f"{sec}s"


def _case_number(ref: str) -> int | None:
    match = re.fullmatch(r"[Cc]?(\d+)", ref.strip())
    return int(match.group(1)) if match else None


class TestRailAdapter(FormatterOnly):
    name = "testrail"
    link_kind = "tms:testrail"
    __test__ = False  # not a pytest class

    def format_cases(self, cases: Sequence[Any]) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CASE_COLUMNS)
        for c in cases:
            refs = " ".join(r for k, r in c.links if k == "requirement")
            writer.writerow(
                [
                    c.title or c.case_key,
                    c.file_path or "",
                    c.priority or "",
                    "Automated",
                    c.description or "",
                    "\n".join(f"{i}. {s}" for i, s in enumerate(c.steps, 1)),
                    "",
                    refs,
                ]
            )
        return buffer.getvalue().encode("utf-8")

    def map_results(
        self, run: Any, results: Sequence[dict[str, Any]], mapping: dict[str, str]
    ) -> MappedResults:
        out, unmapped, omitted = [], [], []
        for r in results:
            tracker_id = mapping.get(r.get("case_id") or "")
            number = _case_number(tracker_id) if tracker_id else None
            if number is None:
                unmapped.append(r["nodeid"])
                continue
            status = STATUS.get(r["outcome"])
            if status is None:
                omitted.append(r["nodeid"])
                continue
            comment = r.get("message") or ""
            if r["outcome"] == "xfailed":
                comment = f"Expected failure (known defect) reproduced. {comment}".strip()
            if (r.get("attempts") or 1) > 1:
                comment = f"Final of {r['attempts']} attempts. {comment}".strip()
            item: dict[str, Any] = {"case_id": number, "status_id": status, "comment": comment}
            if _elapsed(r.get("duration_ms")):
                item["elapsed"] = _elapsed(r.get("duration_ms"))
            if getattr(run, "sut_version", None):
                item["version"] = run.sut_version
            if r.get("defect_ref"):
                item["defects"] = r["defect_ref"]
            out.append(item)
        mapped = MappedResults({"results": out}, unmapped, omitted)
        self.last = mapped
        return mapped

    def format_results(
        self, run: Any, results: Sequence[dict[str, Any]], mapping: dict[str, str]
    ) -> bytes:
        mapped = self.map_results(run, results, mapping)
        return json.dumps(mapped.payload, indent=2, ensure_ascii=False).encode("utf-8")


__all__ = ["TestRailAdapter"]
