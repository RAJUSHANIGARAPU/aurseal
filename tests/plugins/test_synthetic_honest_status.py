"""The synthetic-data plugin earns its status: pass, fail and unknown each mean one thing."""

from __future__ import annotations

import sys

import pytest

from tests.plugins._tier4 import load

PROFILE = {
    "entity": "widget",
    "endpoint": "/api/widgets",
    "fields": [
        {"name": "name", "kind": "string", "required": True, "min_len": 1, "max_len": 300},
        {"name": "size", "kind": "integer", "min": 1, "max": 10},
        {"name": "colour", "kind": "choice", "choices": ["red", "blue"]},
    ],
}


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakeHttp:
    """A target that validates exactly like the profile says, unless told otherwise."""

    def __init__(self, override=None, error=None):
        from test_data.synthetic.profile import Profile

        self.profile = Profile.from_dict(PROFILE)
        self.override = override
        self.error = error
        self.calls = 0

    def post(self, path, json=None):
        self.calls += 1
        if self.error:
            raise self.error
        if self.override:
            status = self.override(json)
            if status is not None:
                return FakeResponse(status)
        return FakeResponse(422 if self.profile.check(json) else 201)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    return tmp_path


def _run(**context):
    plugin = load("synthetic_data_edge").SyntheticDataEdgePlugin()
    base = {"profile": PROFILE, "target": "http://127.0.0.1:9"}
    return plugin.run({**base, **context})


def test_a_target_that_answers_every_label_passes(workspace):
    result = _run(http=FakeHttp())
    assert result.status == "pass"
    assert result.findings[0]["failed"] == 0
    rows = (workspace / "metrics" / "metric_synthetic_probe.jsonl").read_text().splitlines()
    assert len(rows) == result.findings[0]["probed"]


def test_an_accepted_invalid_record_fails():
    lenient = FakeHttp(override=lambda body: 201 if isinstance(body.get("size"), str) else None)
    result = _run(http=lenient)
    assert result.status == "fail"
    assert any(f["broken_constraint"] == "size.type" for f in result.findings[0]["failures"])


def test_a_server_error_fails_even_for_an_invalid_record():
    crashing = FakeHttp(override=lambda body: 500 if body.get("name") == 12345 else None)
    result = _run(http=crashing)
    assert result.status == "fail"
    assert any("server error" in f["detail"] for f in result.findings[0]["failures"])


def test_a_rejected_valid_record_fails():
    strict = FakeHttp(override=lambda body: 422 if "‮" in str(body.get("name")) else None)
    assert _run(http=strict).status == "fail"


@pytest.mark.parametrize(
    "context,reason",
    [
        ({"profile": None}, "no constraint profile"),
        ({"target": None}, "no target"),
        ({"profile": {**PROFILE, "endpoint": None}}, "no endpoint"),
    ],
)
def test_missing_inputs_are_unknown_and_nothing_is_invented(context, reason):
    http = FakeHttp()
    result = _run(http=http, **context)
    assert result.status == "unknown"
    assert reason in result.findings[0]["reason"]
    assert http.calls == 0


def test_an_unreachable_target_is_unknown():
    result = _run(http=FakeHttp(error=ConnectionError("refused")))
    assert result.status == "unknown"
    assert result.findings[0]["unreachable"] == result.findings[0]["probed"]


def test_a_missing_faker_reaches_no_verdict(monkeypatch):
    monkeypatch.setitem(sys.modules, "faker", None)
    http = FakeHttp()
    result = _run(http=http)
    assert result.status == "unknown"
    assert result.findings[0]["faker_available"] is False
    assert http.calls == 0
