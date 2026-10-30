"""Canary generation hook and the run artifact.

A :class:`CanaryRun` mints values that test-data builders embed in what they seed, and
records them with the run's start and end time. The tag comes from ``core.run_id`` (so it
is the same id the run's traffic and results carry) and from the worker prefix, so two
shards never mint the same value.

The IBANs are generated, not typed: ``NL`` + check digits + a fixed synthetic bank code +
a number derived from the tag. They pass mod-97 (a service that validates accepts them) and
no literal account number is committed.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import run_id as run_ids

KIND_NAME = "name"
KIND_EMAIL = "email"
KIND_ACCOUNT = "account"
KINDS = (KIND_NAME, KIND_EMAIL, KIND_ACCOUNT)
EMAIL_DOMAIN = "example.test"
SCHEMA_VERSION = 1
_BANK_CODE = "TEST"


@dataclass(frozen=True)
class Canary:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown canary kind {self.kind!r} (known: {list(KINDS)})")
        if not self.value:
            raise ValueError("a canary value cannot be empty")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_stamp(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def iban_check_digits(country: str, bban: str) -> str:
    """ISO 13616 check digits for ``country`` + ``bban`` (mod-97-10)."""
    digits = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    return f"{98 - int(digits) % 97:02d}"


def synthetic_iban(tag: str, index: int = 0) -> str:
    """A valid-looking IBAN whose account number is derived from ``tag`` and ``index``."""
    seed = hashlib.sha256(f"{tag}/{index}".encode()).hexdigest()
    account = f"{int(seed[:16], 16) % 10**10:010d}"
    bban = f"{_BANK_CODE}{account}"
    return f"NL{iban_check_digits('NL', bban)}{bban}"


@dataclass
class RunArtifact:
    """What the scan needs from a run: the canaries it seeded and when it ran."""

    run_id: str
    started_at: datetime
    ended_at: datetime | None
    canaries: list[Canary] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "run_id": self.run_id,
            "started_at": _stamp(self.started_at),
            "ended_at": _stamp(self.ended_at) if self.ended_at else None,
            "canaries": [{"kind": c.kind, "value": c.value} for c in self.canaries],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RunArtifact:
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError(f"run artifact schema {data.get('schema')!r} is not {SCHEMA_VERSION}")
        try:
            ended = data.get("ended_at")
            return cls(
                run_id=str(data["run_id"]),
                started_at=parse_stamp(data["started_at"]),
                ended_at=parse_stamp(ended) if ended else None,
                canaries=[Canary(c["kind"], c["value"]) for c in data["canaries"]],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"run artifact is missing {exc}") from exc

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target


def load_artifacts(paths: Iterable[str | Path]) -> RunArtifact:
    """One artifact, or the union of several (one per worker or shard of the same run)."""
    loaded = []
    for path in paths:
        try:
            loaded.append(RunArtifact.from_json(json.loads(Path(path).read_text("utf-8"))))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read run artifact {path}: {exc.__class__.__name__}") from exc
    if not loaded:
        raise ValueError("no run artifact given")
    ids = {a.run_id for a in loaded}
    if len(ids) > 1:
        raise ValueError(f"run artifacts belong to different runs: {sorted(ids)}")
    seen: dict[tuple[str, str], Canary] = {}
    for artifact in loaded:
        for canary in artifact.canaries:
            seen.setdefault((canary.kind, canary.value), canary)
    ends = [a.ended_at for a in loaded]
    return RunArtifact(
        run_id=loaded[0].run_id,
        started_at=min(a.started_at for a in loaded),
        ended_at=None if any(e is None for e in ends) else max(e for e in ends if e),
        canaries=list(seen.values()),
    )


class CanaryRun:
    """Mints and records the canaries of one run.

    ``name(i)``, ``email(i)`` and ``iban(i)`` are stable for a given ``(tag, i)``, so a
    builder can call them again and get the value it seeded. Call :meth:`finish` once the
    run's traffic is done, then :meth:`save`.
    """

    def __init__(
        self,
        tag: str | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.run_id = run_ids.run_id(environ) if tag is None else run_ids.sanitize(tag)
        if not self.run_id:
            raise ValueError("a canary run needs a non-empty tag")
        base = run_ids.data_tag(self.run_id, environ) if tag is None else self.run_id
        self.tag = base.lower()
        self.started_at = started_at or _now()
        self.ended_at: datetime | None = None
        self._canaries: dict[tuple[str, str], Canary] = {}

    def _record(self, kind: str, value: str) -> str:
        self._canaries.setdefault((kind, value), Canary(kind, value))
        return value

    def name(self, index: int = 0) -> str:
        suffix = f"-{index}" if index else ""
        return self._record(KIND_NAME, f"canary-{self.tag}{suffix}")

    def email(self, index: int = 0) -> str:
        suffix = f"-{index}" if index else ""
        return self._record(KIND_EMAIL, f"qa+{self.tag}{suffix}@{EMAIL_DOMAIN}")

    def iban(self, index: int = 0) -> str:
        return self._record(KIND_ACCOUNT, synthetic_iban(self.tag, index))

    def finish(self, ended_at: datetime | None = None) -> RunArtifact:
        self.ended_at = ended_at or _now()
        return self.artifact()

    def artifact(self) -> RunArtifact:
        return RunArtifact(
            self.run_id, self.started_at, self.ended_at, list(self._canaries.values())
        )

    def save(self, path: str | Path) -> Path:
        return self.artifact().write(path)
