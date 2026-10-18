"""The release verdict endpoint: served from the release policy, 501 only without one."""

from __future__ import annotations

from qaplatform import schemas
from qaplatform.readers import PolicyReleaseVerdicts, release_verdicts_from_env
from tests.qaplatform._helpers import finish, new_run, push

POLICY = """\
version: 1
default_priority: must
requirements:
  must:
    block: [FAILING]
    no_verdict: [UNCOVERED, NOT_RUN]
detectors:
  block: [signature_mismatch]
defects:
  blocking_severities: [critical]
"""


def _policy(tmp_path) -> str:
    path = tmp_path / "release-policy.yaml"
    path.write_text(POLICY, encoding="utf-8")
    return str(path)


def _passing_run(api, release: str) -> str:
    run_id = new_run(api, release=release)
    push(api, run_id, [{"test_id": "billing::total", "outcome": "passed"}])
    assert finish(api, run_id).payload["integrity"]["verdict"] == "PASS"
    return run_id


def _verdict(api, release: str):
    resp = api("GET", f"/projects/default/releases/{release}/verdict")
    assert resp.status == 200, resp.payload
    assert schemas.errors_for("release-verdict", resp.payload) == []
    return resp.payload


def test_a_trusted_run_and_no_open_defect_is_go(service, api, tmp_path):
    service.release_verdict_reader = PolicyReleaseVerdicts(_policy(tmp_path), open_defects=list)
    run_id = _passing_run(api, "2.4.0")
    doc = _verdict(api, "2.4.0")
    assert (doc["project"], doc["release"], doc["decision"]) == ("default", "2.4.0", "go")
    assert doc["evidence"]["verdict"] == "GO"
    assert doc["evidence"]["evidence_runs"] == [run_id]


def test_an_open_blocking_defect_is_no_go(service, api, tmp_path):
    defects = [{"id": "BUG-1", "severity": "critical", "title": "totals off"}]
    service.release_verdict_reader = PolicyReleaseVerdicts(
        _policy(tmp_path), open_defects=lambda: defects
    )
    _passing_run(api, "2.4.0")
    doc = _verdict(api, "2.4.0")
    assert doc["decision"] == "no_go"
    assert {"rule": "defect:BUG-1", "result": "breached"}.items() <= doc["reasons"][0].items()


def test_a_release_without_runs_is_not_assessed(service, api, tmp_path):
    service.release_verdict_reader = PolicyReleaseVerdicts(_policy(tmp_path), open_defects=list)
    _passing_run(api, "2.4.0")  # another release's evidence never counts
    doc = _verdict(api, "9.9.9")
    assert doc["decision"] == "not_assessed"
    assert any(r["rule"] == "integrity" and r["result"] == "absent" for r in doc["reasons"])


def test_an_unreadable_defect_tracker_is_not_assessed(service, api, tmp_path):
    def tracker():
        raise FileNotFoundError("no defect snapshot")

    service.release_verdict_reader = PolicyReleaseVerdicts(_policy(tmp_path), open_defects=tracker)
    _passing_run(api, "2.4.0")
    assert _verdict(api, "2.4.0")["decision"] == "not_assessed"


def test_without_a_policy_the_endpoint_says_how_to_configure_it(api):
    resp = api("GET", "/projects/default/releases/2.4.0/verdict")
    assert resp.status == 501
    assert "QAP_RELEASE_POLICY" in resp.payload["detail"]


def test_a_configured_policy_that_is_missing_is_501(service, api, tmp_path):
    service.release_verdict_reader = PolicyReleaseVerdicts(tmp_path / "gone.yaml")
    resp = api("GET", "/projects/default/releases/2.4.0/verdict")
    assert resp.status == 501
    assert "gone.yaml" in resp.payload["detail"]


def test_reader_from_env(tmp_path):
    assert release_verdicts_from_env({}, cwd=tmp_path) is None
    explicit = release_verdicts_from_env({"QAP_RELEASE_POLICY": "p.yaml"}, cwd=tmp_path)
    assert explicit is not None and str(explicit.policy) == "p.yaml"
    _policy(tmp_path)
    found = release_verdicts_from_env({}, cwd=tmp_path)
    assert found is not None and found.policy == tmp_path / "release-policy.yaml"
    assert found.waivers == tmp_path / "release-waivers.yaml"
