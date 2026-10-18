"""The requirement marker in a real pytest run: merging, xdist, strictness, outputs."""

from __future__ import annotations

import json

from tests.traceability.conftest import PLUGIN, REGISTRY
from traceability.cli import main as trace_main
from traceability.links import load_case_links

pytest_plugins = ["pytester"]

SAMPLE = """
import pytest

pytestmark = pytest.mark.requirement("REQ-1")

@pytest.mark.requirement("REQ-2")
class TestGroup:
    @pytest.mark.requirement("REQ-3")
    def test_all_three(self):
        pass

    def test_two(self):
        pass

def test_module_only():
    pass
"""


def _tree(pytester, **files):
    pytester.makefile(".yaml", **{"requirements/requirements": REGISTRY})
    pytester.makepyfile(**files)


def _links(pytester, name="case-links.json"):
    return load_case_links(pytester.path / "out" / name)


def test_markers_merge_across_function_class_and_module(pytester, subprocess_env, monkeypatch):
    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    _tree(pytester, test_sample=SAMPLE)
    result = pytester.runpytest_subprocess(*PLUGIN)
    result.assert_outcomes(passed=3)
    doc = _links(pytester)
    got = {link.nodeid.split("::", 1)[1]: set(link.requirements) for link in doc.links}
    assert got == {
        "TestGroup::test_all_three": {"REQ-1", "REQ-2", "REQ-3"},
        "TestGroup::test_two": {"REQ-1", "REQ-2"},
        "test_module_only": {"REQ-1"},
    }
    assert all(link.origin == "marker" and link.case_id for link in doc.links)
    assert doc.invalid == []


def test_xdist_writes_one_file_with_every_link(pytester, subprocess_env, monkeypatch):
    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    _tree(pytester, test_sample=SAMPLE)
    pytester.runpytest_subprocess(*PLUGIN, "-n", "2").assert_outcomes(passed=3)
    files = list((pytester.path / "out").glob("case-links*.json"))
    assert [f.name for f in files] == ["case-links.json"]
    assert len(_links(pytester).links) == 3


def test_a_named_lane_gets_its_own_file(pytester, subprocess_env, monkeypatch):
    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    monkeypatch.setenv("RUN_TRUST_LANE", "browser")
    _tree(pytester, test_sample=SAMPLE)
    pytester.runpytest_subprocess(*PLUGIN).assert_outcomes(passed=3)
    assert (pytester.path / "out" / "case-links-browser.json").is_file()


BAD = """
import pytest

@pytest.mark.requirement("REQ-1")
def test_good():
    pass

@pytest.mark.requirement("REQ-404")
def test_unknown():
    pass

@pytest.mark.requirement("REQ-9")
def test_retired():
    pass

@pytest.mark.requirement("BUG-5")
def test_defect_id():
    pass
"""


def test_a_bad_id_warns_by_default(pytester, subprocess_env, monkeypatch):
    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    _tree(pytester, test_bad=BAD)
    result = pytester.runpytest_subprocess(*PLUGIN)
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["*'REQ-404' is not in the requirements registry*"])
    problems = {(b.id, b.problem) for b in _links(pytester).invalid}
    assert problems == {("REQ-404", "unknown"), ("REQ-9", "retired"), ("BUG-5", "prefix")}


def test_strict_mode_fails_bad_ids_and_passes_good_ones(pytester, subprocess_env, monkeypatch):
    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    monkeypatch.setenv("REQUIREMENTS_STRICT", "true")
    _tree(pytester, test_bad=BAD)
    result = pytester.runpytest_subprocess(*PLUGIN, "-rE")
    # positive control: the valid id passes in the same session
    result.assert_outcomes(passed=1, errors=3)
    result.stdout.fnmatch_lines(["*REQ-404*not in the requirements registry*REQUIREMENTS_STRICT*"])


def test_ids_reach_junit_user_properties(pytester, subprocess_env, monkeypatch):
    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    _tree(pytester, test_sample=SAMPLE)
    pytester.runpytest_subprocess(*PLUGIN, "--junitxml=junit.xml").assert_outcomes(passed=3)
    xml = (pytester.path / "junit.xml").read_text()
    assert 'name="requirements" value="REQ-1,REQ-2,REQ-3"' in xml


VERDICT = """
import pytest
from verification.evidence_verdict import EvidenceVerdict

@pytest.mark.requirement("REQ-1")
def test_named_by_verdict_differs():
    EvidenceVerdict("REQ-2 second thing").proved("observed 3 of 3 rows")

def test_verdict_only():
    EvidenceVerdict("subject", ticket="REQ-3").proved("observed total 12 = 12")
"""


def test_coverage_verdict_links_and_mismatches(pytester, subprocess_env, monkeypatch):
    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    monkeypatch.setenv("VERDICTS_FILE", str(pytester.path / "verdicts.jsonl"))
    _tree(pytester, test_verdict=VERDICT)
    pytester.runpytest_subprocess(*PLUGIN).assert_outcomes(passed=2)
    doc = _links(pytester)
    by_origin = {(link.nodeid.split("::")[1], link.origin): link.requirements for link in doc.links}
    assert by_origin[("test_named_by_verdict_differs", "marker")] == ("REQ-1",)
    assert by_origin[("test_named_by_verdict_differs", "coverage_verdict")] == ("REQ-2",)
    assert by_origin[("test_verdict_only", "coverage_verdict")] == ("REQ-3",)
    assert [m["marker"] for m in doc.mismatches] == [["REQ-1"]]


def test_links_check_cli(pytester, subprocess_env, capsys):
    _tree(pytester, test_bad=BAD)
    assert trace_main(["links", "--check", "--rootdir", str(pytester.path)]) == 0
    assert trace_main(["links", "--check", "--strict", "--rootdir", str(pytester.path)]) == 1
    out = capsys.readouterr().out
    assert "REQ-404 (unknown)" in out and "4 tests, 1 linked, 3 bad" in out


def test_links_check_cli_passes_a_clean_tree(pytester, subprocess_env):
    _tree(pytester, test_sample=SAMPLE)
    assert trace_main(["links", "--check", "--strict", "--rootdir", str(pytester.path)]) == 0


def test_links_are_stored_and_served(pytester, subprocess_env, monkeypatch, tmp_path):
    from results.api import dispatch
    from results.catalogue import build_catalogue, store_catalogue
    from results.store import open_store
    from traceability import api  # noqa: F401 - registers the routes

    monkeypatch.setenv("TRACEABILITY_DIR", str(pytester.path / "out"))
    _tree(pytester, test_sample=SAMPLE)
    pytester.runpytest_subprocess(*PLUGIN).assert_outcomes(passed=3)
    db = tmp_path / "r.db"
    with open_store(str(db)) as store:
        store_catalogue(store, build_catalogue(pytester.path))
    rc = trace_main(
        [
            "links",
            "--reports",
            str(pytester.path / "out"),
            "--db",
            str(db),
            "--rootdir",
            str(pytester.path),
        ]
    )
    assert rc == 0
    with open_store(str(db)) as store:
        case_id = _links(pytester).links[0].case_id
        got = dispatch(store, "GET", f"/cases/{case_id}/requirements")
        assert {r["requirement_id"] for r in got["requirements"]} >= {"REQ-1"}
        put = dispatch(
            store, "PUT", f"/cases/{case_id}/requirements", body={"requirements": ["REQ-7"]}
        )
        assert {"requirement_id": "REQ-7", "source": "import"} in put["requirements"]
        reqs = store.conn.execute("SELECT requirement_id FROM trace_requirements").fetchall()
        assert len(reqs) == 4
    assert json.loads((pytester.path / "out" / "case-links.json").read_text())["schema_version"]
