"""GitHub Issues adapter against a fake session: states, pagination, errors, dedup, create."""

from __future__ import annotations

import pytest

from integrations.trackers.base import DefectDraft, TrackerUnavailable
from integrations.trackers.github import GitHubIssuesTracker
from tests.integrations._fake_http import FakeResponse, FakeSession

REPO = "octo/shop"


def _issue(n, state="open", labels=(), title="t"):
    return {
        "number": n,
        "state": state,
        "title": title,
        "html_url": f"https://example.test/{n}",
        "labels": [{"name": x} for x in labels],
    }


def _tracker(routes, token="tok"):
    session = FakeSession(routes)
    return GitHubIssuesTracker(REPO, token, session=session), session


def test_states_map_open_closed_and_missing():
    t, session = _tracker(
        {
            ("GET", f"/repos/{REPO}/issues/57"): _issue(57, "closed", ["severity:high", "bug"]),
            ("GET", f"/repos/{REPO}/issues/58"): _issue(58),
        }
    )
    states = t.get_states(["GH-57", "GH-58", "GH-99", "GH-x"])
    assert states["GH-57"].state == "closed" and states["GH-57"].severity == "high"
    assert states["GH-58"].state == "open"
    assert states["GH-99"].state == "unknown" and states["GH-x"].state == "unknown"
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer tok"


def test_rate_limit_is_unavailable_not_empty():
    t, _ = _tracker(
        {
            ("GET", f"/repos/{REPO}/issues/1"): FakeResponse(
                403, {}, {"X-RateLimit-Remaining": "0"}
            ),
        }
    )
    with pytest.raises(TrackerUnavailable, match="rate limited"):
        t.get_states(["GH-1"])
    t2, _ = _tracker({("GET", f"/repos/{REPO}/issues/1"): FakeResponse(500, {})})
    with pytest.raises(TrackerUnavailable):
        t2.get_states(["GH-1"])


def test_list_open_paginates_and_skips_pull_requests():
    pages = {
        1: [_issue(i) for i in range(100)],
        2: [_issue(100), {"number": 101, "state": "open", "pull_request": {}}],
    }
    t, session = _tracker(
        {
            ("GET", f"/repos/{REPO}/issues"): lambda kw: pages[kw["params"]["page"]],
        }
    )
    got = t.list_open(["bug"])
    assert len(got) == 101 and got[-1].id == "GH-100"
    assert session.calls[0][2]["params"]["labels"] == "bug"


def test_find_by_fingerprint_and_create():
    created = {}

    def create(kw):
        created.update(kw["json"])
        return _issue(7, title=kw["json"]["title"])

    t, session = _tracker(
        {
            ("GET", "/search/issues"): lambda kw: (
                {"items": [_issue(3)]} if "aaa" in kw["params"]["q"] else {"items": []}
            ),
            ("POST", f"/repos/{REPO}/issues"): create,
        }
    )
    assert t.find_by_fingerprint("aaa").id == "GH-3"
    assert t.find_by_fingerprint("bbb") is None
    ref = t.create(DefectDraft("title x", "body", "bbb", ("bug",)))
    assert ref.id == "GH-7" and created == {"title": "title x", "body": "body", "labels": ["bug"]}


def test_a_repo_is_required():
    with pytest.raises(ValueError):
        GitHubIssuesTracker("", None, session=FakeSession())
