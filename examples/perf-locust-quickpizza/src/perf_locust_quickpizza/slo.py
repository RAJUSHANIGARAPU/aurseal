"""Post-run SLO check: read Locust's CSV stats and fail on a breached budget.

Locust's own exit code only says whether any request failed (``--exit-code-on-error``);
it knows nothing about latency. The profiles set that exit code to 0 and leave the
verdict to this module, which holds each profile to the budgets in ``slo.json``::

    python -m perf_locust_quickpizza.slo smoke      # reads reports/smoke_stats.csv
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROFILES = ("smoke", "load", "stress")
SLO_FILE = Path(__file__).resolve().parents[2] / "slo.json"
REPORTS = Path("reports")


class Budget(BaseModel):
    """What one profile must stay within. Unknown keys are typos, so they are errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    p95_ms: float = Field(gt=0, description="95th percentile response time, milliseconds")
    # Above 5 % errors a budget stops describing a service anyone would call healthy.
    error_rate: float = Field(gt=0, le=0.05, description="Share of failed requests, 0-1")


@dataclass(frozen=True)
class Measured:
    requests: int
    failures: int
    p95_ms: float | None

    @property
    def error_rate(self) -> float:
        return self.failures / self.requests if self.requests else 0.0


def load_budgets(path: Path = SLO_FILE) -> dict[str, Budget]:
    """Every profile's budget; ``ValueError`` names the first problem found."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: expected an object keyed by profile")
    missing = [profile for profile in PROFILES if profile not in raw]
    unknown = sorted(set(raw) - set(PROFILES))
    if missing or unknown:
        raise ValueError(f"{path.name}: missing profiles {missing}, unknown profiles {unknown}")
    try:
        return {profile: Budget.model_validate(raw[profile]) for profile in PROFILES}
    except ValidationError as error:
        raise ValueError(f"{path.name}: {error}") from error


def read_stats(path: Path) -> Measured:
    """The ``Aggregated`` row of a Locust ``*_stats.csv`` file."""
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Name") == "Aggregated":
                p95 = row.get("95%", "")
                return Measured(
                    requests=int(row["Request Count"]),
                    failures=int(row["Failure Count"]),
                    p95_ms=float(p95) if p95 not in ("", "N/A") else None,
                )
    raise ValueError(f"{path}: no Aggregated row; is this a Locust stats file?")


def breaches(measured: Measured, budget: Budget) -> list[str]:
    """Every way ``measured`` misses ``budget``; empty means the SLO was met."""
    if measured.requests == 0:
        return ["no requests were measured: a run that measured nothing cannot pass"]
    found = []
    if measured.p95_ms is None:
        found.append("no p95 was recorded")
    elif measured.p95_ms > budget.p95_ms:
        found.append(f"p95 {measured.p95_ms:g} ms exceeds the {budget.p95_ms:g} ms budget")
    if measured.error_rate > budget.error_rate:
        found.append(
            f"error rate {measured.error_rate:.2%} exceeds the {budget.error_rate:.2%} budget"
        )
    return found


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (1, 2) or args[0] not in PROFILES:
        print("usage: python -m perf_locust_quickpizza.slo smoke|load|stress [STATS_CSV]")
        return 2
    profile = args[0]
    stats = Path(args[1]) if len(args) == 2 else REPORTS / f"{profile}_stats.csv"
    measured = read_stats(stats)
    budget = load_budgets()[profile]
    print(
        f"{profile}: {measured.requests} requests, {measured.error_rate:.2%} failed, "
        f"p95 {measured.p95_ms} ms (budget p95 < {budget.p95_ms:g} ms, "
        f"errors < {budget.error_rate:.2%})"
    )
    found = breaches(measured, budget)
    for line in found:
        print(f"BREACHED {line}")
    if not found:
        print("met: every SLO for this profile")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
