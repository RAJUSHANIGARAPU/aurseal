"""JUnit import from pytest, JUnit5, Jest and Go layouts; unsafe XML is refused."""

from __future__ import annotations

from pathlib import Path

import pytest

from results.api import ApiError, dispatch
from results.cli import main
from results.io.junit import JUnitError, import_junit, iter_testcases, pytest_nodeid

FIX = Path(__file__).parent / "fixtures" / "junit"


def _final(store, run_id):
    return {r["nodeid"]: r for r in store.query("run_final_results", run_id=run_id)}


def test_pytest_layout_maps_back_to_node_ids(store):
    report = import_junit(store, FIX / "pytest.xml", framework="pytest", run_id="j-py")
    assert report.run_ids == ["j-py"] and report.added["result"] == 5
    rows = _final(store, "j-py")
    assert rows["tests/test_billing.py::TestInvoice::test_pay[card]"]["outcome"] == "passed"
    assert rows["tests/test_billing.py::TestInvoice::test_pay[card]"]["param_id"] == "card"
    refund = rows["tests/test_billing.py::TestInvoice::test_refund"]
    assert refund["outcome"] == "failed" and refund["message"] == "AssertionError: assert 0 == 5"
    assert refund["duration_ms"] == 300 and "assert refund() == 5" in refund["longrepr"]
    assert rows["tests/test_search.py::test_empty"]["outcome"] == "skipped"
    assert rows["tests/test_search.py::test_empty"]["skip_reason"] == "no index"
    assert rows["tests/test_search.py::test_known"]["outcome"] == "xfailed"
    assert rows["tests/test_search.py::test_setup_breaks"]["outcome"] == "error"
    ev = store.query("run_evidence", run_id="j-py")
    assert [(e["kind"], e["uri"]) for e in ev] == [("screenshot", "reports/shots/refund.png")]
    links = store.query(
        "case_links_for",
        case_id=rows["tests/test_billing.py::TestInvoice::test_pay[card]"]["case_id"],
    )
    assert [(r["kind"], r["ref"]) for r in links] == [("requirement", "REQ-12")]
    run = store.run("j-py")
    assert run.source == "junit-import" and run.started_at == "2026-01-05T10:00:00.000Z"


def test_pytest_suite_name_is_detected_without_the_flag(store):
    import_junit(store, FIX / "pytest.xml", run_id="auto")
    assert store.run("auto").framework == "pytest"
    assert "tests/test_search.py::test_empty" in _final(store, "auto")


def test_junit5_layout(store):
    import_junit(store, FIX / "junit5.xml", framework="junit5", run_id="j5")
    rows = _final(store, "j5")
    bad = rows["com.example.LoginTest::rejectsBadPassword()"]
    assert bad["outcome"] == "failed" and bad["message"] == "expected: <401> but was: <200>"
    assert "login attempt for user-7" in bad["longrepr"]
    assert rows["com.example.LoginTest::lockout()"]["outcome"] == "skipped"
    assert store.run("j5").framework == "junit5"


def test_jest_layout_uses_the_first_text_line_and_properties(store):
    import_junit(store, FIX / "jest.xml", framework="jest", run_id="js")
    rows = _final(store, "js")
    failed = next(r for r in rows.values() if r["outcome"] == "failed")
    assert failed["message"] == "Error: expect(received).toBe(expected)"
    good = next(r for r in rows.values() if r["outcome"] == "passed")
    case = store.query("case_get", case_id=good["case_id"])[0]
    assert case["owner"] == "team-web"


def test_go_layout(store):
    import_junit(store, FIX / "go.xml", framework="go", run_id="go")
    rows = _final(store, "go")
    assert rows["cart::TestTotal/with_discount"]["outcome"] == "failed"
    assert rows["cart::TestEmpty"]["outcome"] == "skipped"
    assert len(rows) == 3


def test_import_is_idempotent_with_a_content_derived_run_id(store):
    first = import_junit(store, FIX / "go.xml", framework="go")
    again = import_junit(store, FIX / "go.xml", framework="go")
    assert first.run_ids == again.run_ids and first.run_ids[0].startswith("junit-")
    assert again.added["result"] == 0 and again.added.get("invocation", 0) == 0
    assert store.run(first.run_ids[0]).status == "finished"


@pytest.mark.parametrize("name", ["bomb.xml", "external.xml"])
def test_doctype_and_entities_are_refused(store, name):
    with pytest.raises(JUnitError, match="DOCTYPE or ENTITY"):
        import_junit(store, FIX / name)


def test_not_junit_is_refused():
    with pytest.raises(JUnitError):
        list(iter_testcases(b"<html><body/></html>"))
    with pytest.raises(JUnitError):
        list(iter_testcases(b"<testsuite"))


def test_unnamed_testcases_are_reported_not_dropped(store):
    xml = (
        b'<testsuite name="s"><testcase classname="a"/>'
        b'<testcase classname="a" name="ok"/></testsuite>'
    )
    report = import_junit(store, xml, run_id="u")
    assert report.added["result"] == 1
    assert report.rejected == [{"classname": "a", "reason": "testcase without a name"}]


def test_pytest_nodeid_rules():
    assert (
        pytest_nodeid("tests.api.test_x.TestA.TestB", "test_c")
        == "tests/api/test_x.py::TestA::TestB::test_c"
    )
    assert (
        pytest_nodeid("tests.test_x", "test_c", file="tests/test_x.py") == "tests/test_x.py::test_c"
    )


def test_rest_and_cli(store, tmp_path, capsys):
    body = (FIX / "go.xml").read_text()
    report = dispatch(
        store, "POST", "/imports/junit", params={"framework": "go", "run_id": "g2"}, body=body
    )
    assert report["run_ids"] == ["g2"]
    with pytest.raises(ApiError):
        dispatch(store, "POST", "/imports/junit", body=(FIX / "bomb.xml").read_text())
    assert main(["import", "junit", str(FIX / "junit5.xml"), "--framework", "junit5"]) == 0
    assert main(["import", "junit", str(FIX / "bomb.xml")]) == 1


def test_log_analyzer_reuses_the_safe_reader():
    from core.ai.log_analyzer import LogAnalyzer

    failures = LogAnalyzer().parse_failures(FIX / "pytest.xml")
    assert {f["test"] for f in failures} == {"test_refund", "test_setup_breaks"}
    assert "error" in LogAnalyzer().parse_failures(FIX / "bomb.xml")[0]
