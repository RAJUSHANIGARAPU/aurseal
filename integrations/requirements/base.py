"""Where requirements come from: the repository YAML, or issues carrying a label.

Every source returns :class:`traceability.requirements.Requirement` records; the sync
(``traceability.requirements_sync``) merges them and writes the snapshot the marker check
reads, so validation never needs the network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests

from integrations.http import ServiceUnavailable
from integrations.read_transport import ReadOnlyTrackerTransport, github_transport, paginate
from traceability.requirements import Namespace, Registry, Requirement


class RequirementSource(Protocol):
    name: str

    def fetch(self) -> Iterable[Requirement]: ...


class SourceUnavailable(RuntimeError):
    """A source could not be read; the sync stops instead of retiring everything."""


@dataclass
class LocalYamlSource:
    path: Path
    name: str = "local"

    def fetch(self) -> list[Requirement]:
        if not Path(self.path).is_file():
            raise SourceUnavailable(f"{self.path}: no requirements file")
        return list(Registry.load(self.path))


@dataclass
class GitHubIssuesSource:
    """Issues with ``label`` are requirements; the id is the title prefix (``REQ-12: ...``).

    ``number_prefix`` (e.g. ``GHR``) gives issues without an id in the title the id
    ``GHR-<number>``; without it they are skipped and reported. A milestone becomes the
    release label, ``priority:<p>`` labels the priority, and an issue closed as not planned
    is retired. An issue labelled ``container_label`` (default ``epic``), or one that has
    sub-issues, is a container: ``kind: epic``, too coarse for a test to link to.

    Reads go through a read-only transport limited to the repository's issues.
    """

    repo: str
    label: str = "requirement"
    token: str | None = None
    session: requests.Session | None = None
    number_prefix: str | None = None
    name: str = "github"
    container_label: str | None = "epic"
    transport: ReadOnlyTrackerTransport | None = None
    skipped: list[str] = field(default_factory=list)

    def fetch(self) -> list[Requirement]:
        if not self.repo or "/" not in self.repo:
            raise SourceUnavailable(f"GitHub source needs 'owner/name', got {self.repo!r}")
        reader = self.transport or github_transport(
            (f"/repos/{self.repo}/issues",), self.token, self.session
        )
        try:
            issues = list(
                paginate(
                    reader, f"/repos/{self.repo}/issues", {"state": "all", "labels": self.label}
                )
            )
        except ServiceUnavailable as exc:
            raise SourceUnavailable(str(exc)) from exc
        ns = Namespace.from_env()
        out = []
        self.skipped = []
        for issue in issues:
            if "pull_request" in issue:
                continue
            title = str(issue.get("title") or "")
            match = re.match(r"^\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*[:\-–]\s*(.+)$", title)
            if match and ns.is_requirement_id(match.group(1)):
                req_id, text = match.group(1), match.group(2).strip()
            elif self.number_prefix:
                req_id, text = f"{self.number_prefix}-{issue['number']}", title.strip()
            else:
                self.skipped.append(f"#{issue.get('number')}: no requirement id in the title")
                continue
            labels = [
                str(lbl.get("name") if isinstance(lbl, dict) else lbl)
                for lbl in issue.get("labels") or ()
            ]
            priority = next((x.split(":", 1)[1] for x in labels if x.startswith("priority:")), None)
            retired = issue.get("state") == "closed" and issue.get("state_reason") == "not_planned"
            milestone = issue.get("milestone") or {}
            sub_issues = (issue.get("sub_issues_summary") or {}).get("total") or 0
            container = bool(sub_issues) or (
                self.container_label is not None and self.container_label in labels
            )
            out.append(
                Requirement(
                    id=req_id,
                    title=text,
                    status="retired" if retired else "active",
                    priority=priority,
                    labels=tuple(x for x in labels if x != self.label and ":" not in x),
                    release=milestone.get("title"),
                    source=self.name,
                    source_ref=f"#{issue.get('number')}",
                    url=issue.get("html_url"),
                    kind="epic" if container else "requirement",
                )
            )
        return out
