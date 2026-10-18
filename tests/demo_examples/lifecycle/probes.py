"""Drive a parametrised seed fixture as a probe: record where (and whether) it was refused.

:class:`test_data.fixture_replay.FixtureSeeder` is for building data and fails on the first
refusal. A probe instead wants the refusal as its observation: which step refused, with
which status and message. Each step's outcome becomes a
:class:`oracles.rejection.RejectionSignals`, so "accepted" means no surface signalled a
refusal, not merely "no exception".
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from api.clients.http_engine import HttpEngine
from api.engine.context_memory import resolve_path
from oracles.rejection import RejectionSignals
from test_data.fixture_replay import Fixture, bind, run_day_values


@dataclass
class ProbeResult:
    values: dict[str, Any]
    refused_at: str | None = None
    signals: RejectionSignals = field(default_factory=RejectionSignals)
    message: str = ""

    @property
    def accepted(self) -> bool:
        return self.refused_at is None

    def describe(self) -> str:
        where = "accepted" if self.accepted else f"refused at '{self.refused_at}'"
        return f"{where}: {self.signals.describe()} {self.message}".strip()


def run_probe(
    api: HttpEngine, fixture: Fixture, params: Mapping[str, Any], *, today: date
) -> ProbeResult:
    """Replay ``fixture`` step by step; stop at the first refused step and say why."""
    values: dict[str, Any] = dict(params)
    builtins = run_day_values(today)
    steps = ([fixture.create] if fixture.create else []) + list(fixture.steps)
    result = ProbeResult(values)
    for step in steps:
        path = bind(step.path, values, builtins)
        body = bind(copy.deepcopy(step.body), values, builtins)
        response = api.request(step.method, path, json=body)
        result.signals = RejectionSignals(api_status=response.status)
        if not response.is_success:
            result.refused_at = step.name
            try:
                result.message = str(response.json().get("message", ""))
            except ValueError:
                result.message = response.text[:200]
            return result
        payload = response.json()
        for name, dotted in step.capture.items():
            values[name] = resolve_path(payload, dotted)
    return result
