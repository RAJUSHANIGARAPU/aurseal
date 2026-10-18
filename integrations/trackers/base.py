"""The defect tracker seam: one protocol, a prefix map that picks the adapter for an id.

Ids keep the ``PREFIX-n`` shape the detectors already validate. The prefix map
(``integrations/trackers.yaml``) is the single authority on which prefix belongs to which
tracker; a requirement prefix (``REQ``) is refused there, so a requirement can never be
read as a defect.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml

State = Literal["open", "closed", "unknown"]
CONFIG_ENV = "DEFECT_TRACKERS_FILE"
DEFAULT_CONFIG = Path("integrations") / "trackers.yaml"
_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class TrackerUnavailable(RuntimeError):
    """The tracker could not be read: fall back to the last snapshot, never assume closed."""


@dataclass(frozen=True)
class DefectState:
    id: str
    state: State
    title: str = ""
    severity: str | None = None
    labels: tuple[str, ...] = ()
    url: str | None = None
    tracker: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "title": self.title,
            "severity": self.severity,
            "labels": list(self.labels),
            "url": self.url,
            "tracker": self.tracker,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> DefectState:
        return cls(
            id=str(data["id"]),
            state=data.get("state", "unknown"),
            title=str(data.get("title") or ""),
            severity=data.get("severity"),
            labels=tuple(data.get("labels") or ()),
            url=data.get("url"),
            tracker=str(data.get("tracker") or ""),
        )


DefectRef = DefectState


@dataclass(frozen=True)
class DefectDraft:
    title: str
    body: str
    fingerprint: str
    labels: tuple[str, ...] = ()
    nodeid: str = ""
    run_id: str = ""
    kind: str = "failure"  # failure | closed_regressed

    def to_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "fingerprint": self.fingerprint,
            "labels": list(self.labels),
            "nodeid": self.nodeid,
            "run_id": self.run_id,
            "kind": self.kind,
        }


class DefectTracker(Protocol):
    name: str

    def get_states(self, ids: Collection[str]) -> dict[str, DefectState]: ...

    def find_by_fingerprint(self, fp: str) -> DefectRef | None: ...

    def create(self, draft: DefectDraft) -> DefectRef: ...

    def list_open(self, labels: Collection[str] = ()) -> list[DefectRef]: ...


@dataclass
class TrackerConfig:
    trackers: dict[str, dict[str, Any]] = field(default_factory=dict)
    prefixes: dict[str, str] = field(default_factory=dict)  # prefix -> tracker name
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None, *, rootdir: Path | None = None) -> TrackerConfig:
        root = Path(rootdir) if rootdir is not None else Path.cwd()
        target = Path(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)
        target = target if target.is_absolute() else root / target
        if not target.is_file():
            return cls(path=target)
        text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), target.read_text("utf-8"))
        data = yaml.safe_load(text) or {}
        return cls.from_body(data, path=target, rootdir=root)

    @classmethod
    def from_body(
        cls, data: Mapping[str, Any], *, path: Path | None = None, rootdir: Path | None = None
    ) -> TrackerConfig:
        from traceability.requirements import Namespace

        req_prefixes = Namespace.from_env().prefixes
        trackers: dict[str, dict[str, Any]] = {}
        prefixes: dict[str, str] = {}
        for name, spec in (data.get("trackers") or {}).items():
            spec = dict(spec or {})
            if spec.get("type") not in ("file", "github"):
                raise ValueError(f"tracker {name!r}: type must be 'file' or 'github'")
            if spec.get("type") == "file" and rootdir is not None:
                p = Path(spec.get("path") or "defects/defects.yaml")
                spec["path"] = str(p if p.is_absolute() else Path(rootdir) / p)
            trackers[name] = spec
            for prefix in spec.get("prefixes") or ():
                if prefix in req_prefixes:
                    raise ValueError(f"tracker {name!r}: {prefix!r} is a requirement prefix")
                if prefix in prefixes:
                    raise ValueError(f"prefix {prefix!r} is mapped to two trackers")
                prefixes[str(prefix)] = name
        return cls(trackers, prefixes, path)

    def tracker_for(self, defect_id: str) -> str | None:
        return self.prefixes.get(defect_id.split("-", 1)[0])


def build(name: str, spec: Mapping[str, Any], session: Any = None) -> DefectTracker:
    if spec["type"] == "file":
        from integrations.trackers.file import FileTracker

        return FileTracker(Path(spec.get("path") or "defects/defects.yaml"), name=name)
    from integrations.trackers.github import GitHubIssuesTracker

    token = os.environ.get("DEFECT_TRACKER_TOKEN") or os.environ.get("GITHUB_TOKEN")
    return GitHubIssuesTracker(
        str(spec.get("repo") or ""),
        token,
        session=session,
        name=name,
        prefix=(spec.get("prefixes") or ["GH"])[0],
        labels=tuple(spec.get("labels") or ()),
        severity_prefix=str(spec.get("severity_label_prefix") or "severity:"),
    )


def resolve(defect_id: str, config: TrackerConfig, session: Any = None) -> DefectTracker | None:
    """The adapter for ``defect_id`` by its prefix; None when no tracker owns the prefix."""
    name = config.tracker_for(defect_id)
    return None if name is None else build(name, config.trackers[name], session)
