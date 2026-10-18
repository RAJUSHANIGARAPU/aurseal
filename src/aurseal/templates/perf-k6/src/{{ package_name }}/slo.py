"""The service level objectives in ``k6/slo.json``, validated.

k6 reads the same file (``k6/thresholds.js``) and turns each budget into a threshold. The
offline tests use this module so that a typo or a meaningless budget fails before a run,
not after k6 has quietly accepted a threshold nobody meant.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROFILES = ("smoke", "load", "stress")
SLO_FILE = Path(__file__).resolve().parents[2] / "k6" / "slo.json"


class Budget(BaseModel):
    """What one profile must stay within. Unknown keys are typos, so they are errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    p95_ms: float = Field(gt=0, description="95th percentile request duration, milliseconds")
    # Above 5 % errors a budget stops describing a service anyone would call healthy.
    error_rate: float = Field(gt=0, le=0.05, description="Share of failed requests, 0-1")
    abort_on_fail: bool = False
    delay_abort_eval: str = Field(default="10s", pattern=r"^\d+(ms|s|m)$")


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
