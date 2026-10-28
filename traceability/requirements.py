"""Requirement ids and the requirements registry.

A requirement id is ``PREFIX-n`` (default pattern ``^[A-Z][A-Z0-9]*-\\d+$``) whose prefix
is one of the requirement prefixes (default ``REQ``). The prefix is what keeps the two
namespaces apart: ``REQ-12`` is a requirement, ``BUG-142`` a defect, and neither is
accepted where the other belongs.

The registry is a reviewed file in the repository (``requirements/requirements.yaml``)::

    requirements:
      - id: REQ-1
        title: A subscription can be created
        priority: must          # must | should | could
        status: active          # active | retired
        labels: [subscriptions]
        release: "1.0"
        kind: requirement       # requirement (default) | epic | container

An ``epic`` or ``container`` entry groups requirements; a test linked to it directly is
reported as "link too coarse" (report only).

``Registry.status(id)`` answers ``active``, ``retired`` or ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, NewType

from traceability._util import load_yaml, read_json, validate

RequirementId = NewType("RequirementId", str)
Status = Literal["active", "retired", "unknown"]

PATTERN_ENV = "REQUIREMENT_ID_PATTERN"
PREFIXES_ENV = "REQUIREMENT_ID_PREFIXES"
FILE_ENV = "REQUIREMENTS_FILE"
STRICT_ENV = "REQUIREMENTS_STRICT"
DEFAULT_PATTERN = r"^[A-Z][A-Z0-9]*-\d+$"
DEFAULT_PREFIXES = ("REQ",)
DEFAULT_FILE = Path("requirements") / "requirements.yaml"
SNAPSHOT_FILE = Path("requirements") / "snapshot.json"
PRIORITIES = ("must", "should", "could")


class InvalidRequirementId(ValueError):
    """An id that is not a requirement id (wrong shape or a non-requirement prefix)."""


@dataclass(frozen=True)
class Namespace:
    """What a requirement id looks like: a format and the prefixes reserved for requirements."""

    pattern: re.Pattern[str]
    prefixes: frozenset[str]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Namespace:
        env = os.environ if env is None else env
        raw = (env.get(PATTERN_ENV) or "").strip() or DEFAULT_PATTERN
        prefixes = [p.strip() for p in (env.get(PREFIXES_ENV) or "").split(",") if p.strip()]
        return cls(re.compile(raw), frozenset(prefixes or DEFAULT_PREFIXES))

    def problem(self, raw: object) -> str | None:
        """``None`` for a good id, else ``"format"`` or ``"prefix"``."""
        if not isinstance(raw, str) or not self.pattern.fullmatch(raw.strip()):
            return "format"
        if raw.strip().split("-", 1)[0] not in self.prefixes:
            return "prefix"
        return None

    def is_requirement_id(self, raw: object) -> bool:
        return self.problem(raw) is None


def parse_id(raw: str, namespace: Namespace | None = None) -> RequirementId:
    """``raw`` stripped, as a :data:`RequirementId`; raises :class:`InvalidRequirementId`."""
    ns = namespace or Namespace.from_env()
    problem = ns.problem(raw)
    if problem == "format":
        raise InvalidRequirementId(f"{raw!r} does not match {ns.pattern.pattern}")
    if problem == "prefix":
        raise InvalidRequirementId(
            f"{raw!r} is not a requirement id: its prefix is not one of "
            f"{sorted(ns.prefixes)} (set {PREFIXES_ENV} to change them)"
        )
    return RequirementId(raw.strip())


def ids_in_verdict(record: Mapping[str, Any], namespace: Namespace | None = None) -> list[str]:
    """Requirement ids a coverage verdict names: its ticket, or its subject's first word."""
    ns = namespace or Namespace.from_env()
    found: list[str] = []
    ticket = record.get("ticket")
    if isinstance(ticket, str) and ns.is_requirement_id(ticket):
        found.append(ticket.strip())
    subject = record.get("subject")
    if isinstance(subject, str) and subject.strip():
        head = subject.strip().split()[0].rstrip(":,;")
        if ns.is_requirement_id(head) and head not in found:
            found.append(head)
    return found


@dataclass(frozen=True)
class Requirement:
    id: str
    title: str
    status: Literal["active", "retired"] = "active"
    priority: str | None = None
    labels: tuple[str, ...] = ()
    release: str | None = None
    parent_id: str | None = None
    source: str = "local"
    source_ref: str | None = None
    url: str | None = None
    kind: str = "requirement"  # requirement | epic | container

    @property
    def content_hash(self) -> str:
        """Hash of what a reader sees (title, status, priority): changes flag linked cases."""
        text = json.dumps([self.title, self.status, self.priority], ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["labels"] = list(self.labels)
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any], *, source: str | None = None) -> Requirement:
        labels = data.get("labels") or ()
        return cls(
            id=str(data["id"]).strip(),
            title=str(data.get("title") or "").strip(),
            status="retired" if data.get("status") == "retired" else "active",
            priority=data.get("priority"),
            labels=tuple(str(x) for x in labels),
            release=None if data.get("release") is None else str(data["release"]),
            parent_id=data.get("parent_id"),
            source=source or str(data.get("source") or "local"),
            source_ref=data.get("source_ref"),
            url=data.get("url"),
            kind=str(data.get("kind") or "requirement"),
        )


@dataclass
class Registry:
    """Requirements by id, plus where they came from."""

    requirements: dict[str, Requirement] = field(default_factory=dict)
    path: Path | None = None
    present: bool = True  # False: the registry file does not exist

    def __iter__(self):
        return iter(self.requirements.values())

    def __len__(self) -> int:
        return len(self.requirements)

    def get(self, requirement_id: str) -> Requirement | None:
        return self.requirements.get(requirement_id)

    def status(self, requirement_id: str) -> Status:
        req = self.requirements.get(requirement_id)
        return "unknown" if req is None else req.status

    def active(self) -> list[Requirement]:
        return [r for r in self.requirements.values() if r.status == "active"]

    @classmethod
    def from_requirements(cls, items: Iterable[Requirement], path: Path | None = None) -> Registry:
        reg = cls({}, path)
        for item in items:
            if item.id in reg.requirements:
                raise ValueError(f"requirement {item.id} is listed twice")
            reg.requirements[item.id] = item
        return reg

    @classmethod
    def load(cls, path: str | Path | None = None, *, rootdir: Path | None = None) -> Registry:
        """Load ``path``, else ``$REQUIREMENTS_FILE``, else the repository default.

        The default prefers ``requirements/snapshot.json`` (written by the requirements
        sync when outside sources are configured) over the hand-edited YAML. A missing
        file gives an empty registry with ``present=False``: every id is then ``unknown``,
        which the marker check reports.
        """
        target = resolve_path(path, rootdir)
        if not target.is_file():
            return cls({}, target, present=False)
        if target.suffix == ".json":
            data = read_json(target)
            if not isinstance(data, dict):
                raise ValueError(f"{target}: not a JSON object")
            validate(data, "requirements-snapshot.schema.json")
        else:
            data = load_yaml(target) or {"requirements": []}
            validate(data, "requirements.schema.json")
        return cls.from_requirements(
            (Requirement.from_json(item) for item in data.get("requirements") or ()), target
        )


def resolve_path(path: str | Path | None = None, rootdir: Path | None = None) -> Path:
    root = Path(rootdir) if rootdir is not None else Path.cwd()
    if path is not None:
        target = Path(path)
    elif os.environ.get(FILE_ENV, "").strip():
        target = Path(os.environ[FILE_ENV].strip())
    elif (root / SNAPSHOT_FILE).is_file():
        target = SNAPSHOT_FILE
    else:
        target = DEFAULT_FILE
    return target if target.is_absolute() else root / target
