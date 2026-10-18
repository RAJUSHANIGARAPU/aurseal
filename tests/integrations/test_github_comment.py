"""Comment upsert against a fake session: create once, update by marker, 403 is logged."""

from __future__ import annotations

from integrations.github import upsert_pr_comment
from tests.integrations._fake_http import FakeResponse, FakeSession

REPO, PR, MARK = "octo/shop", 7, "<!-- test-run-summary -->"


def _session():
    comments: list[dict] = [{"id": 1, "body": "unrelated"}]

    def create(kw):
        item = {"id": 100 + len(comments), "body": kw["json"]["body"], "html_url": "u"}
        comments.append(item)
        return FakeResponse(201, item)

    def update(kw):
        comments[-1]["body"] = kw["json"]["body"]
        return FakeResponse(200, comments[-1])

    routes = {
        ("GET", f"/repos/{REPO}/issues/{PR}/comments"): lambda kw: list(comments),
        ("POST", f"/repos/{REPO}/issues/{PR}/comments"): create,
        ("PATCH", f"/repos/{REPO}/issues/comments/101"): update,
    }
    return FakeSession(routes), comments


def test_creates_once_then_updates_in_place():
    session, comments = _session()
    first = upsert_pr_comment(REPO, PR, "body one", MARK, "tok", session)
    second = upsert_pr_comment(REPO, PR, f"{MARK}\nbody two", MARK, "tok", session)
    assert first.created and not second.created and first.id == second.id == 101
    assert len(comments) == 2 and comments[-1]["body"] == f"{MARK}\nbody two"
    assert comments[-1]["body"].startswith(MARK)


def test_forbidden_returns_none():
    session = FakeSession(
        {
            ("GET", f"/repos/{REPO}/issues/{PR}/comments"): lambda kw: [],
            ("POST", f"/repos/{REPO}/issues/{PR}/comments"): FakeResponse(403, {}),
        }
    )
    assert upsert_pr_comment(REPO, PR, "x", MARK, "tok", session) is None
