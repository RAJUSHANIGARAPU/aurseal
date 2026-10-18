"""A load profile: how load is applied (stages) and what must hold (thresholds).

A profile without thresholds is refused. k6 exits non-zero only on a breached threshold
or a script error, so a run with no thresholds can only ever "pass": it would measure
and judge nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A k6 metric name, optionally with a tag filter: ``http_req_duration{status:200}``.
_METRIC = re.compile(r"^[a-z][a-z0-9_]*(\{[a-z0-9_]+:[A-Za-z0-9_.\-/]+\})?$")
#: A k6 threshold expression: ``p(95)<500``, ``rate<0.01``, ``avg<=200``.
_EXPR = re.compile(
    r"^(p\(\d{1,2}(\.\d+)?\)|avg|min|max|med|rate|count|value)\s*(<=|>=|<|>|===|==|!=)\s*"
    r"\d+(\.\d+)?$"
)
_DURATION = re.compile(r"^\d+(\.\d+)?(ms|s|m|h)$")
_UNIT_S = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


class ProfileError(ValueError):
    """The profile cannot be judged: missing or malformed thresholds or stages."""


@dataclass(frozen=True)
class Threshold:
    metric: str
    expr: str

    def __post_init__(self) -> None:
        if not _METRIC.match(self.metric):
            raise ProfileError(f"not a k6 metric name: {self.metric!r}")
        if not _EXPR.match(self.expr.strip()):
            raise ProfileError(f"not a k6 threshold expression: {self.expr!r}")

    @property
    def label(self) -> str:
        return f"{self.metric}: {self.expr.strip()}"


@dataclass(frozen=True)
class LoadProfile:
    """``stages`` are ``(duration, target_vus)`` pairs, k6's ramping shape."""

    stages: tuple[tuple[str, int], ...]
    thresholds: tuple[Threshold, ...]
    think_time_s: float = 0.0
    name: str = "profile"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.thresholds:
            raise ProfileError("no thresholds: nothing to judge")
        if not self.stages:
            raise ProfileError("no stages: no load would be applied")
        for duration, target in self.stages:
            if not _DURATION.match(str(duration)):
                raise ProfileError(f"not a k6 duration: {duration!r}")
            if not isinstance(target, int) or isinstance(target, bool) or target < 0:
                raise ProfileError(f"stage target must be a non-negative int, got {target!r}")
        if max(t for _, t in self.stages) == 0:
            raise ProfileError("every stage targets 0 virtual users: no load would be applied")
        if self.think_time_s < 0:
            raise ProfileError("think_time_s cannot be negative")

    @property
    def duration_s(self) -> float:
        total = 0.0
        for duration, _ in self.stages:
            match = re.match(r"^(\d+(?:\.\d+)?)(ms|s|m|h)$", duration)
            assert match  # validated above
            total += float(match.group(1)) * _UNIT_S[match.group(2)]
        return total

    @property
    def vus_max(self) -> int:
        return max(t for _, t in self.stages)

    def k6_thresholds(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self.thresholds:
            out.setdefault(t.metric, []).append(t.expr.strip())
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LoadProfile:
        if not isinstance(raw, dict):
            raise ProfileError("a profile must be a JSON object")
        try:
            stages = tuple((str(s[0]), s[1]) for s in raw.get("stages") or ())
        except (TypeError, IndexError, KeyError) as exc:
            raise ProfileError(f"stages must be [duration, target] pairs: {exc}") from exc
        thresholds = tuple(
            Threshold(metric=str(t.get("metric", "")), expr=str(t.get("expr", "")))
            for t in raw.get("thresholds") or ()
        )
        return cls(
            stages=stages,
            thresholds=thresholds,
            think_time_s=float(raw.get("think_time_s", 0.0)),
            name=str(raw.get("name") or "profile"),
        )

    @classmethod
    def from_json(cls, path: Path | str) -> LoadProfile:
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProfileError(f"cannot read profile {path}: {exc}") from exc
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stages": [list(s) for s in self.stages],
            "thresholds": [{"metric": t.metric, "expr": t.expr} for t in self.thresholds],
            "think_time_s": self.think_time_s,
        }
