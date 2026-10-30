"""The target map: named targets (a ticket, a service) -> the log sources that hold their logs.

```yaml
targets:
  billing:
    sources:
      - logs/billing                # a plain string is a source
  orders:
    sources:
      - source: logs/shared.log     # one source shared by several targets...
        include: "[orders]"         # ...split by a marker: only lines containing it count
        exclude: "healthcheck"      # and lines containing this never do
```

What a ``source`` means depends on the backend: a path or glob (files), a stream selector
(Loki), a log group name (CloudWatch). Markers are plain, case-sensitive substrings.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Source:
    source: str
    include: str | None = None
    exclude: str | None = None

    def accepts(self, line: str) -> bool:
        if self.include and self.include not in line:
            return False
        return not (self.exclude and self.exclude in line)


@dataclass(frozen=True)
class Target:
    name: str
    sources: tuple[Source, ...] = field(default_factory=tuple)


def _source(raw: Any, where: str) -> Source:
    if isinstance(raw, str) and raw:
        return Source(raw)
    if isinstance(raw, Mapping) and isinstance(raw.get("source"), str) and raw["source"]:
        unknown = set(raw) - {"source", "include", "exclude"}
        if unknown:
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
        for key in ("include", "exclude"):
            if raw.get(key) is not None and not (isinstance(raw[key], str) and raw[key]):
                raise ValueError(f"{where}: {key} must be a non-empty string")
        return Source(raw["source"], raw.get("include"), raw.get("exclude"))
    raise ValueError(f"{where}: a source is a string or a mapping with a 'source' key")


def parse_targets(data: Any) -> list[Target]:
    raw = data.get("targets") if isinstance(data, Mapping) else None
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("the target map needs a non-empty 'targets' mapping")
    targets = []
    for name, body in raw.items():
        sources = body.get("sources") if isinstance(body, Mapping) else body
        if not isinstance(sources, list):
            raise ValueError(f"target {name!r}: 'sources' must be a list")
        targets.append(
            Target(
                str(name),
                tuple(_source(s, f"target {name!r} source {i}") for i, s in enumerate(sources)),
            )
        )
    return targets


def load_targets(path: str | Path) -> list[Target]:
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
        data = json.loads(text) if file.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read target map {file}: {exc.__class__.__name__}") from exc
    return parse_targets(data)
