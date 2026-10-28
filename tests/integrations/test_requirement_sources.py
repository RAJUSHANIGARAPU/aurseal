"""Requirement sources: the repository YAML and labelled GitHub issues."""

from __future__ import annotations

import pytest

from integrations.requirements.base import GitHubIssuesSource, LocalYamlSource, SourceUnavailable
from tests.integrations._fake_http import FakeResponse, FakeSession

REPO = "octo/shop"


def _issue(n, title, state="open", reason=None, labels=("requirement",), milestone=None):
    return {
        "number": n,
        "title": title,
        "state": state,
        "state_reason": reason,
        "labels": [{"name": x} for x in labels],
        "html_url": f"https://example.test/{n}",
        "milestone": {"title": milestone} if milestone else None,
    }


ISSUES = [
    _issue(
        1,
        "REQ-1: A customer can be created",
        labels=("requirement", "priority:must", "customers"),
        milestone="1.0",
    ),
    _issue(2, "REQ-2 - Cancellation refunds", state="closed", reason="not_planned"),
    _issue(3, "REQ-3: Done and shipped", state="closed", reason="completed"),
    _issue(4, "Untitled idea"),
    {"number": 5, "title": "REQ-5: a pull request", "pull_request": {}},
]


def _source(**kw):
    session = FakeSession({("GET", f"/repos/{REPO}/issues"): lambda q: ISSUES})
    return GitHubIssuesSource(REPO, session=session, **kw), session


def test_ids_titles_states_and_labels():
    source, session = _source()
    got = {r.id: r for r in source.fetch()}
    assert set(got) == {"REQ-1", "REQ-2", "REQ-3"}
    assert got["REQ-1"].title == "A customer can be created" and got["REQ-1"].priority == "must"
    assert got["REQ-1"].release == "1.0" and got["REQ-1"].labels == ("customers",)
    assert got["REQ-2"].status == "retired" and got["REQ-3"].status == "active"
    assert got["REQ-1"].source == "github" and got["REQ-1"].source_ref == "#1"
    assert source.skipped == ["#4: no requirement id in the title"]
    assert session.calls[0][2]["params"]["labels"] == "requirement"
    assert session.calls[0][2]["params"]["state"] == "all"


def test_a_number_prefix_maps_untitled_issues():
    source, _ = _source(number_prefix="GHR")
    from traceability.requirements import Namespace

    assert "GHR-4" in {r.id for r in source.fetch()}
    assert Namespace.from_env().problem("GHR-4") == "prefix"  # must be added to the prefixes


def test_an_unreachable_source_raises():
    session = FakeSession({("GET", f"/repos/{REPO}/issues"): FakeResponse(502, {})})
    with pytest.raises(SourceUnavailable):
        GitHubIssuesSource(REPO, session=session).fetch()
    with pytest.raises(SourceUnavailable):
        GitHubIssuesSource("", session=session).fetch()


def test_local_source(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text("requirements:\n  - {id: REQ-1, title: one}\n")
    assert [r.id for r in LocalYamlSource(path).fetch()] == ["REQ-1"]
    with pytest.raises(SourceUnavailable):
        LocalYamlSource(tmp_path / "none.yaml").fetch()


def test_an_epic_label_or_sub_issues_make_a_container():
    issues = [
        _issue(10, "REQ-10: Billing", labels=("requirement", "epic")),
        {**_issue(11, "REQ-11: Payments"), "sub_issues_summary": {"total": 2}},
        _issue(12, "REQ-12: Refund rounding"),
    ]
    session = FakeSession({("GET", f"/repos/{REPO}/issues"): lambda q: issues})
    kinds = {r.id: r.kind for r in GitHubIssuesSource(REPO, session=session).fetch()}
    assert kinds == {"REQ-10": "epic", "REQ-11": "epic", "REQ-12": "requirement"}
    session = FakeSession({("GET", f"/repos/{REPO}/issues"): lambda q: issues[:1]})
    plain = GitHubIssuesSource(REPO, session=session, container_label=None).fetch()
    assert [r.kind for r in plain] == ["requirement"]
