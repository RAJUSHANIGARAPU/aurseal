"""One pull-request comment, created once and updated in place (own ~40 lines, no action).

A hidden marker identifies the comment, so every run edits the same comment instead of
appending a new one. A missing permission (a fork's read-only token) is logged and
returns None: a comment that cannot be posted must not fail the build.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from integrations.http import GitHubClient, ServiceUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommentRef:
    id: int
    url: str
    created: bool


def upsert_pr_comment(
    repo: str,
    pr: int,
    body: str,
    marker: str,
    token: str,
    session: requests.Session | None = None,
) -> CommentRef | None:
    if marker not in body:
        body = f"{marker}\n{body}"
    client = GitHubClient(token, session)
    try:
        existing = next(
            (
                c
                for c in client.paginate(f"/repos/{repo}/issues/{pr}/comments", {})
                if marker in str(c.get("body") or "")
            ),
            None,
        )
        if existing is None:
            response = client.request(
                "POST", f"/repos/{repo}/issues/{pr}/comments", json={"body": body}
            )
        else:
            response = client.request(
                "PATCH", f"/repos/{repo}/issues/comments/{existing['id']}", json={"body": body}
            )
    except ServiceUnavailable as exc:
        logger.warning("[pr-comment] not posted: %s", exc)
        return None
    if response.status_code >= 400:
        logger.warning(
            "[pr-comment] not posted: HTTP %s (a fork's token cannot comment; the step "
            "summary has the same text)",
            response.status_code,
        )
        return None
    data = response.json()
    return CommentRef(int(data["id"]), str(data.get("html_url") or ""), existing is None)
