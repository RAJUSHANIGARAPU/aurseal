"""A small docs directory and store for the living-docs tests."""

from __future__ import annotations

from pathlib import Path

from results.models import CaseRecord, ResultRecord, RunRecord

PAGE = """---
feature: billing
requirements: [REQ-12]
---
# Billing

Invoices are created monthly.

<!-- living:status -->

## Cases

<!-- living:cases feature=billing -->

## Requirement REQ-12

<!-- living:cases requirement=REQ-12 -->

- Paid invoices are final.
- Refunds use [the refund page](refunds.md).
"""


def write_docs(tmp_path: Path, page: str = PAGE) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "billing.md").write_text(page)
    (docs / "plain.md").write_text("# Plain page\n\nNo front matter.\n")
    return docs


def seed_cases(store) -> None:
    store.upsert_cases(
        [
            CaseRecord(
                "c-pay",
                "t.py::test_pay",
                title="Pay an invoice",
                owner="team-pay",
                links=(("feature", "billing"), ("requirement", "REQ-12")),
            ),
            CaseRecord(
                "c-list", "t.py::test_list", title="List invoices", links=(("feature", "billing"),)
            ),
        ]
    )


def run(store, run_id: str, day: int, outcomes: dict[str, str], verdict: str | None = "PASS"):
    store.begin_run(RunRecord(run_id=run_id, started_at=f"2026-01-{day:02d}T10:00:00.000Z"))
    store.add_results(
        ResultRecord(run_id, f"t.py::test_{name}", out, case_id=f"c-{name}")
        for name, out in outcomes.items()
    )
    store.finish_run(run_id, "finished", verdict)
