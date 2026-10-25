"""The pytest plugin: inert by default, and the upload it writes with --aurseal-out."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aurseal.importers import registry
from aurseal.model.identity import fingerprint
from aurseal.model.v1 import RunUpload, Status
from aurseal.pytest_plugin import Recorder, split_nodeid

pytest_plugins = ["pytester"]

SUITE = """
import pytest

COUNTER = __import__("pathlib").Path(__file__).with_name("attempts.txt")


@pytest.fixture
def broken_setup():
    raise RuntimeError("database is down")


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("cleanup failed")


class TestCart:
    @pytest.mark.parametrize("currency", ["EUR-10", "USD-20"])
    def test_total(self, currency):
        assert currency != "USD-20", "wrong total"

    @pytest.mark.case("SHOP-7")
    def test_pay(self):
        pass


def test_setup_error(broken_setup):
    pass


def test_teardown_error(broken_teardown):
    pass


@pytest.mark.skip(reason="coupon service not ready")
def test_coupon():
    pass


@pytest.mark.xfail(reason="BUG-142 rounding")
def test_rounding():
    assert 0.1 + 0.2 == 0.3


@pytest.mark.xfail(reason="was broken")
def test_fixed_meanwhile():
    pass


@pytest.mark.xfail(reason="must fail", strict=True)
def test_strict_xpass():
    pass


def test_property(record_property):
    record_property("case_id", "SHOP-9")


@pytest.mark.flaky(reruns=2)
def test_flaky():
    runs = int(COUNTER.read_text()) if COUNTER.exists() else 0
    COUNTER.write_text(str(runs + 1))
    assert runs >= 1, "first run fails"


@pytest.mark.flaky(reruns=1)
def test_always_red():
    assert False, "still red"
"""


def _run(pytester, *args):
    pytester.makepyfile(test_shop=SUITE)
    out = pytester.path / "reports" / "upload.json"
    result = pytester.runpytest("-p", "no:cacheprovider", f"--aurseal-out={out}", *args)
    upload = RunUpload.model_validate_json(out.read_text(encoding="utf-8"))
    return result, out, {(r.suite, r.name, r.variant): r for r in upload.records}


def test_without_the_option_the_plugin_writes_nothing(pytester):
    pytester.makepyfile(test_one="def test_one():\n    pass\n")
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    assert not [p for p in pytester.path.rglob("*.json")]
    assert "aurseal upload" not in result.stdout.str()


def test_the_option_is_registered_and_documented_in_help(pytester):
    result = pytester.runpytest("--help")
    result.stdout.fnmatch_lines(["*--aurseal-out=PATH*"])


def test_statuses_reasons_and_attempts(pytester):
    result, out, records = _run(pytester)
    result.stdout.fnmatch_lines([f"*aurseal upload: {out}*"])
    assert len(records) == 12

    assert records[("TestCart", "test_total", "EUR-10")].status is Status.PASSED
    red = records[("TestCart", "test_total", "USD-20")]
    assert (red.status, red.reason) == (Status.FAILED, None)
    assert "wrong total" in (red.message or "")

    setup = records[("", "test_setup_error", None)]
    assert (setup.status, setup.message) == (Status.BROKEN, "RuntimeError: database is down")
    assert records[("", "test_teardown_error", None)].status is Status.BROKEN

    skipped = records[("", "test_coupon", None)]
    assert (skipped.status, skipped.message) == (Status.SKIPPED, "coupon service not ready")
    xfail = records[("", "test_rounding", None)]
    assert (xfail.status, xfail.reason, xfail.message) == (
        Status.SKIPPED,
        "xfail",
        "BUG-142 rounding",
    )
    xpass = records[("", "test_fixed_meanwhile", None)]
    assert (xpass.status, xpass.reason) == (Status.PASSED, "xpass")
    strict = records[("", "test_strict_xpass", None)]
    assert (strict.status, strict.reason) == (Status.FAILED, "xpass_strict")

    for record in records.values():
        assert record.framework == "pytest" and record.path == "test_shop.py"
        assert record.duration_ms is not None


def test_reruns_are_attempts_of_one_record(pytester):
    _, _, records = _run(pytester)
    flaky = records[("", "test_flaky", None)]
    assert [a.status for a in flaky.attempts] == [Status.FAILED, Status.PASSED]
    assert [a.attempt_no for a in flaky.attempts] == [0, 1]
    assert flaky.status is Status.PASSED and flaky.flaky_in_run
    red = records[("", "test_always_red", None)]
    assert [a.status for a in red.attempts] == [Status.FAILED, Status.FAILED]
    assert red.status is Status.FAILED and not red.flaky_in_run


# pytest-rerunfailures before 16.7 logs no teardown for a rerun attempt; 16.7 and later do.
@pytest.mark.parametrize(
    "rerun_attempt",
    [
        [("setup", "passed"), ("call", "rerun")],
        [("setup", "passed"), ("call", "rerun"), ("teardown", "passed")],
    ],
    ids=["rerun-without-teardown", "rerun-with-teardown"],
)
def test_a_rerun_closes_one_attempt_with_or_without_its_teardown(tmp_path, rerun_attempt):
    recorder = Recorder(tmp_path / "upload.json")
    last = [("setup", "passed"), ("call", "passed"), ("teardown", "passed")]
    for when, outcome in rerun_attempt + last:
        recorder.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid="test_x.py::test_flaky",
                when=when,
                outcome=outcome,
                duration=0.0,
                longrepr="AssertionError: first run fails" if outcome == "rerun" else None,
                user_properties=[],
            )
        )
    (record,) = recorder.upload().records
    assert [a.status for a in record.attempts] == [Status.FAILED, Status.PASSED]
    assert record.status is Status.PASSED and record.flaky_in_run


def test_explicit_ids_and_parameters(pytester):
    _, _, records = _run(pytester)
    assert records[("TestCart", "test_pay", None)].explicit_id == "SHOP-7"
    assert records[("", "test_property", None)].explicit_id == "SHOP-9"
    totals = [r for r in records.values() if r.name == "test_total"]
    assert sorted(r.variant for r in totals) == ["EUR-10", "USD-20"]
    assert {r.fingerprint for r in totals} == {
        fingerprint("pytest", "test_shop.py", "TestCart", "test_total")
    }


def test_the_upload_and_the_junit_xml_agree_on_identity(pytester):
    junit = pytester.path / "junit.xml"
    _, out, records = _run(pytester, f"--junitxml={junit}")
    assert registry.detect(out.read_bytes()[:4096]).name == "aurseal"
    from_upload = {r.identity: r.status for r in registry.parse_file(out)}
    assert from_upload == {r.identity: r.status for r in records.values()}
    from_junit = {r.identity: r.status for r in registry.parse_file(junit)}
    # The JUnit XML has no xpass/teardown distinction to lose; identities must match exactly.
    assert set(from_junit) == set(from_upload)


def test_xdist_workers_report_to_one_file(pytester):
    pytest.importorskip("xdist")
    _, out, records = _run(pytester, "-n", "2")
    assert len(records) == 12
    assert records[("TestCart", "test_pay", None)].explicit_id == "SHOP-7"
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"].startswith("1.")


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        ("tests/a/test_x.py::test_y", ("tests/a/test_x.py", [], "test_y", None)),
        ("t/test_x.py::A::B::test_y[1-a::b]", ("t/test_x.py", ["A", "B"], "test_y", "1-a::b")),
        ("docs/guide.txt", ("docs/guide.txt", [], "guide.txt", None)),
    ],
)
def test_split_nodeid(nodeid, expected):
    assert split_nodeid(nodeid) == expected


def test_an_upload_of_another_major_is_refused(tmp_path):
    from aurseal.importers import ReportError

    target = tmp_path / "upload.json"
    target.write_text('{"schema_version": "2.0", "records": []}', encoding="utf-8")
    with pytest.raises(ReportError, match="schema_version"):
        registry.parse_file(target)
