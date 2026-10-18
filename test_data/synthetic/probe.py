"""Send synthetic records to an endpoint and judge each answer against its label.

=================  ================  =========
record expected    response          verdict
=================  ================  =========
accept             2xx               pass
accept             4xx               fail      a valid record was rejected
reject             4xx               pass
reject             2xx               fail      an invalid record was accepted
either             5xx               fail      a server error is never a correct answer
either             no response       unreachable
either             anything else     fail
=================  ================  =========

``probe`` never raises on a transport error: an unreachable target is a result
(``unreachable``), so the caller can report "could not measure" instead of a
pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from test_data.synthetic.generator import SyntheticRecord

PASS = "pass"
FAIL = "fail"
UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ProbeResult:
    record: SyntheticRecord
    status: int | None
    verdict: str
    detail: str

    @property
    def expected(self) -> str:
        return self.record.expected

    def metric_row(self, profile_name: str) -> dict[str, Any]:
        return {
            "profile": profile_name,
            "mode": self.record.mode,
            "seed": self.record.seed,
            "record_index": self.record.index,
            "strategy": self.record.strategy,
            "broken_constraint": self.record.broken_constraint,
            "expected": self.expected,
            "observed_status": self.status,
            "verdict": self.verdict,
        }


def judge(expected: str, status: int | None) -> tuple[str, str]:
    if status is None:
        return UNREACHABLE, "no response"
    if status >= 500:
        return FAIL, f"server error {status}"
    if 200 <= status < 300:
        if expected == "accept":
            return PASS, f"accepted ({status})"
        return FAIL, f"invalid record accepted ({status})"
    if 400 <= status < 500:
        if expected == "reject":
            return PASS, f"rejected ({status})"
        return FAIL, f"valid record rejected ({status})"
    return FAIL, f"unexpected status {status}"


def probe(endpoint: str, http, records: Iterable[SyntheticRecord]) -> list[ProbeResult]:
    """POST every record's values to ``endpoint`` through ``http`` (``.post(path, json=)``)."""
    results: list[ProbeResult] = []
    for record in records:
        try:
            response = http.post(endpoint, json=record.values)
            status = int(response.status)
        except Exception as exc:  # transport failure is a result, not a crash
            results.append(ProbeResult(record, None, UNREACHABLE, f"{type(exc).__name__}: {exc}"))
            continue
        verdict, detail = judge(record.expected, status)
        results.append(ProbeResult(record, status, verdict, detail))
    return results


def summarise(results: Sequence[ProbeResult]) -> dict[str, Any]:
    failures = [r for r in results if r.verdict == FAIL]
    return {
        "probed": len(results),
        "passed": sum(r.verdict == PASS for r in results),
        "failed": len(failures),
        "unreachable": sum(r.verdict == UNREACHABLE for r in results),
        "failures": [
            {
                "strategy": r.record.strategy,
                "broken_constraint": r.record.broken_constraint,
                "status": r.status,
                "detail": r.detail,
                "index": r.record.index,
                "mode": r.record.mode,
            }
            for r in failures[:25]
        ],
    }
