"""The read-only tracker transport: GET one host's allowed paths, refuse the rest unsent."""

from __future__ import annotations

from pathlib import Path

import pytest

from gate.read_only import READ_ONLY_MODULES, find_write_calls
from integrations.read_transport import ReadOnlyTrackerTransport, TransportRefused
from integrations.requirements.base import GitHubIssuesSource
from integrations.trackers.github import GitHubIssuesTracker
from tests.integrations._fake_http import FakeResponse

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://tracker.example.test/api"
REPO = "octo/shop"


class RecordingSession:
    """Records every attribute touched and every call made; answers nothing useful."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return FakeResponse(200, [])

    def __getattr__(self, name):  # any other verb would be recorded here
        def record(*args, **kw):
            self.calls.append((name.upper(), args, kw))

        return record


def _transport(session):
    return ReadOnlyTrackerTransport(BASE, ["/search/issues", f"/repos/{REPO}/issues"], session)


def test_an_allowed_path_is_read():
    session = RecordingSession()
    _transport(session).get("/search/issues", {"q": "x"})
    assert session.calls == [
        ("GET", f"{BASE}/search/issues", {"headers": {}, "params": {"q": "x"}, "timeout": 20})
    ]


@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete", "head", "options", "request"])
def test_no_verb_but_get_exists_and_the_session_is_never_touched(verb):
    session = RecordingSession()
    transport = _transport(session)
    with pytest.raises(AttributeError):
        getattr(transport, verb)
    with pytest.raises(AttributeError):
        transport._session  # noqa: B018 - the session itself is not kept
    assert session.calls == []


@pytest.mark.parametrize(
    "path",
    [
        "https://elsewhere.example.test/api/search/issues",
        "//elsewhere.example.test/search/issues",
        "/repos/octo/other/issues",
        "/repos/octo/shop/pulls",
        "/search/issuesx",
        "/repos/octo/shop/issues/../../other/issues",
        "/user",
    ],
)
def test_a_foreign_host_or_path_is_refused_before_any_io(path):
    session = RecordingSession()
    with pytest.raises(TransportRefused):
        _transport(session).get(path)
    assert session.calls == []


def test_an_absolute_url_on_the_same_host_is_allowed():
    session = RecordingSession()
    _transport(session).get(f"{BASE}/repos/{REPO}/issues/4")
    assert session.calls[0][1] == f"{BASE}/repos/{REPO}/issues/4"


def test_the_transport_is_immutable():
    transport = _transport(RecordingSession())
    with pytest.raises(AttributeError):
        transport.allowed_paths = ("/",)


def test_tracker_reads_cannot_reach_another_repository():
    tracker = GitHubIssuesTracker(REPO, None, session=RecordingSession())
    with pytest.raises(TransportRefused):
        tracker.reader.get("/repos/octo/other/issues/1")


def test_requirement_sync_reads_through_the_transport():
    session = RecordingSession()
    source = GitHubIssuesSource(REPO, session=session)
    source.fetch()
    assert {c[0] for c in session.calls} == {"GET"}


def test_read_only_modules_have_no_write_call():
    assert find_write_calls([ROOT / m for m in READ_ONLY_MODULES]) == []


def test_the_write_call_check_bites(tmp_path):
    # Positive control: the same check over a planted write finds it.
    planted = tmp_path / "planted.py"
    planted.write_text("def f(s):\n    return s.post('/repos/o/r/issues')\n")
    assert [c.call for c in find_write_calls([planted])] == ["s.post"]
