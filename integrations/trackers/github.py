"""GitHub Issues as the defect tracker: ``GH-57`` is issue #57 of the configured repo.

Reads need no write permission (``issues: read``); ``create`` is only called by an
explicit ``--submit`` or by CI auto-filing when ``DEFECT_AUTOFILE=true`` on ``main``.
GitHub has no native severity: a label such as ``severity:high`` carries it.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import requests

from integrations.http import GitHubClient, ServiceUnavailable
from integrations.trackers.base import DefectDraft, DefectRef, DefectState, TrackerUnavailable

FINGERPRINT_TAG = "defect-fingerprint"


class GitHubIssuesTracker:
    def __init__(
        self,
        repo: str,
        token: str | None,
        session: requests.Session | None = None,
        *,
        name: str = "github",
        prefix: str = "GH",
        labels: tuple[str, ...] = (),
        severity_prefix: str = "severity:",
    ) -> None:
        if not repo or "/" not in repo:
            raise ValueError(f"GitHub tracker needs 'owner/name', got {repo!r}")
        self.repo = repo
        self.name = name
        self.prefix = prefix
        self.labels = labels
        self.severity_prefix = severity_prefix
        self.client = GitHubClient(token, session)

    def _id(self, number: int) -> str:
        return f"{self.prefix}-{number}"

    def _state(self, issue: dict[str, Any]) -> DefectState:
        labels = tuple(
            str(lbl.get("name") if isinstance(lbl, dict) else lbl)
            for lbl in issue.get("labels") or ()
        )
        severity = next(
            (
                lbl[len(self.severity_prefix) :]
                for lbl in labels
                if lbl.startswith(self.severity_prefix)
            ),
            None,
        )
        return DefectState(
            id=self._id(int(issue["number"])),
            state="closed" if issue.get("state") == "closed" else "open",
            title=str(issue.get("title") or ""),
            severity=severity,
            labels=labels,
            url=issue.get("html_url"),
            tracker=self.name,
        )

    def get_states(self, ids: Collection[str]) -> dict[str, DefectState]:
        out: dict[str, DefectState] = {}
        for defect_id in ids:
            number = defect_id.split("-", 1)[-1]
            if not number.isdigit():
                out[defect_id] = DefectState(defect_id, "unknown", tracker=self.name)
                continue
            try:
                response = self.client.request("GET", f"/repos/{self.repo}/issues/{number}")
            except ServiceUnavailable as exc:
                raise TrackerUnavailable(str(exc)) from exc
            if response.status_code == 404:
                out[defect_id] = DefectState(defect_id, "unknown", tracker=self.name)
            elif response.status_code >= 400:
                raise TrackerUnavailable(f"issue {number}: HTTP {response.status_code}")
            else:
                out[defect_id] = self._state(response.json())
        return out

    def find_by_fingerprint(self, fp: str) -> DefectRef | None:
        query = f'repo:{self.repo} is:issue "{FINGERPRINT_TAG}: {fp}" in:body'
        try:
            data = self.client.json("GET", "/search/issues", params={"q": query})
        except ServiceUnavailable as exc:
            raise TrackerUnavailable(str(exc)) from exc
        items = data.get("items") or []
        return self._state(items[0]) if items else None

    def create(self, draft: DefectDraft) -> DefectRef:
        body = {"title": draft.title, "body": draft.body, "labels": list(draft.labels)}
        try:
            issue = self.client.json("POST", f"/repos/{self.repo}/issues", json=body)
        except ServiceUnavailable as exc:
            raise TrackerUnavailable(str(exc)) from exc
        return self._state(issue)

    def list_open(self, labels: Collection[str] = ()) -> list[DefectRef]:
        params = {"state": "open"}
        wanted = tuple(labels) or self.labels
        if wanted:
            params["labels"] = ",".join(wanted)
        try:
            issues = list(self.client.paginate(f"/repos/{self.repo}/issues", params))
        except ServiceUnavailable as exc:
            raise TrackerUnavailable(str(exc)) from exc
        return [self._state(i) for i in issues if "pull_request" not in i]
