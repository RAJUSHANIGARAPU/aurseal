"""Contract test reporting.

Generates:
  - A structured JSON report (machine-readable, suitable for CI artifacts)
  - A plain-text summary (printed to stdout during CI runs)

Reports are written to reports/contracts/ with a timestamp suffix so they
accumulate without overwriting each other.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contract_testing.models import ContractDiff, ValidationResult

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("reports/contracts")


class ContractReporter:
    """Writes contract test reports to disk and stdout."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self._dir = output_dir or _REPORTS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Diff report (consumer ↔ provider version comparison)
    # ------------------------------------------------------------------

    def write_diff_report(
        self,
        consumer: str,
        provider: str,
        old_version: str,
        new_version: str,
        diff: ContractDiff,
    ) -> Path:
        """Write a JSON diff report and return its path."""
        run_ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = self._dir / f"diff_{consumer}_{provider}_{run_ts}.json"

        report = {
            "consumer": consumer,
            "provider": provider,
            "old_version": old_version,
            "new_version": new_version,
            "change_type": diff.change_type.value,
            "breaking_changes": diff.breaking,
            "non_breaking_changes": diff.non_breaking,
            "timestamp": run_ts,
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self._print_diff_summary(consumer, provider, old_version, new_version, diff)
        return report_path

    def _print_diff_summary(
        self,
        consumer: str,
        provider: str,
        old_version: str,
        new_version: str,
        diff: ContractDiff,
    ) -> None:
        print(f"\n[contract] {consumer} ↔ {provider}: {old_version} → {new_version}")
        if diff.change_type.value == "none":
            print("  No changes detected.")
            return
        if diff.breaking:
            print(f"  BREAKING ({len(diff.breaking)}):")
            for change in diff.breaking:
                print(f"    ✗ {change}")
        if diff.non_breaking:
            print(f"  Non-breaking ({len(diff.non_breaking)}):")
            for change in diff.non_breaking:
                print(f"    + {change}")

    # ------------------------------------------------------------------
    # Validation report (provider-side test run)
    # ------------------------------------------------------------------

    def write_validation_report(
        self,
        consumer: str,
        provider: str,
        results: list[ValidationResult],
    ) -> Path:
        """Write a validation report and return its path."""
        run_ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = self._dir / f"validation_{consumer}_{provider}_{run_ts}.json"

        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]

        report = {
            "consumer": consumer,
            "provider": provider,
            "timestamp": run_ts,
            "summary": {
                "total": len(results),
                "passed": len(passed),
                "failed": len(failed),
            },
            "failures": [{"interaction": r.interaction_key, "errors": r.errors} for r in failed],
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self._print_validation_summary(consumer, provider, results)
        return report_path

    def _print_validation_summary(
        self, consumer: str, provider: str, results: list[ValidationResult]
    ) -> None:
        failed = [r for r in results if not r.passed]
        passed_count = len(results) - len(failed)
        print(
            f"\n[contract] Provider validation {consumer} ↔ {provider}: "
            f"{passed_count}/{len(results)} passed"
        )
        for r in failed:
            print(f"  FAIL {r.interaction_key}")
            for err in r.errors:
                print(f"       {err}")

    # ------------------------------------------------------------------
    # Session summary (end of pytest run)
    # ------------------------------------------------------------------

    def generate_session_summary(
        self,
        consumer: str,
        provider: str,
        contracts_saved: int,
        validations: list[ValidationResult],
    ) -> str:
        failed = sum(1 for v in validations if not v.passed)
        if failed:
            return (
                f"[contract] {contracts_saved} contract(s) saved. "
                f"{failed}/{len(validations)} provider validation(s) FAILED."
            )
        return (
            f"[contract] {contracts_saved} contract(s) saved. "
            f"All {len(validations)} provider validation(s) passed."
        )
