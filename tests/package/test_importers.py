"""JUnit XML and Playwright JSON importers, and the registry that picks between them."""

from __future__ import annotations

import io
from collections import Counter
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from aurseal.importers import ReportError, registry
from aurseal.model.v1 import Status

FIXTURES = Path(__file__).parent / "fixtures"
PYTEST_XML = FIXTURES / "junit-pytest.xml"
SUREFIRE_XML = FIXTURES / "junit-surefire.xml"
PLAYWRIGHT_JSON = FIXTURES / "playwright.json"
ALL = [PYTEST_XML, SUREFIRE_XML, PLAYWRIGHT_JSON]


def _by_name(records):
    return {(r.name, r.variant, r.configuration): r for r in records}


@pytest.mark.parametrize("fixture", ALL, ids=lambda p: p.name)
def test_twenty_reimports_create_no_duplicate_identities(fixture):
    first = registry.parse_file(fixture)
    identities = [r.identity for r in first]
    assert len(identities) == len(set(identities)), "one import holds a duplicate result key"
    cases = {r.explicit_id or r.fingerprint for r in first}
    for _ in range(19):
        again = registry.parse_file(fixture)
        assert [r.identity for r in again] == identities
        assert {r.explicit_id or r.fingerprint for r in again} == cases


def test_pytest_parameters_are_one_case_with_variants():
    records = [r for r in registry.parse_file(PYTEST_XML) if r.name == "test_total"]
    assert sorted(r.variant for r in records) == ["EUR-10", "GBP-30", "USD-20"]
    assert len({r.fingerprint for r in records}) == 1
    record = records[0]
    assert (record.framework, record.path, record.suite) == (
        "pytest",
        "tests/shop/test_cart.py",
        "TestCart",
    )


def test_pytest_statuses_and_reasons():
    records = _by_name(registry.parse_file(PYTEST_XML))
    assert records[("test_total", "GBP-30", None)].status is Status.FAILED
    assert records[("test_total", "GBP-30", None)].message == "assert 31 == 30"
    assert records[("test_empty_cart", None, None)].status is Status.BROKEN
    assert records[("test_coupon_later", None, None)].status is Status.SKIPPED
    xfail = records[("test_known_rounding", None, None)]
    assert (xfail.status, xfail.reason) == (Status.SKIPPED, "xfail")
    paid = records[("test_pay", None, None)]
    assert paid.explicit_id == "SHOP-7"


def test_repeated_testcases_are_attempts_of_one_result():
    records = [r for r in registry.parse_file(PYTEST_XML) if r.name == "test_retry_me"]
    assert len(records) == 1
    record = records[0]
    assert [a.status for a in record.attempts] == [Status.FAILED, Status.PASSED]
    assert record.status is Status.PASSED and record.flaky_in_run
    assert record.duration_ms == 41


def test_surefire_reruns_and_case_properties():
    records = _by_name(registry.parse_file(SUREFIRE_XML))
    partial = records[("partialRefund", None, None)]
    assert (partial.framework, partial.path) == ("junit", "com.example.shop.RefundTest")
    assert [a.status for a in partial.attempts] == [Status.FAILED, Status.PASSED]
    assert partial.flaky_in_run
    assert records[("fullRefund", None, None)].explicit_id == "SHOP-12"
    assert records[("refundByAmount(int)", "1", None)].status is Status.PASSED


def test_a_surefire_name_without_the_method_is_refused():
    xml = b'<testsuite name="s"><testcase classname="a.B" name="[1] 5" /></testsuite>'
    with pytest.raises(ReportError, match="no method name"):
        list(registry.parse(io.BytesIO(xml)))


def test_playwright_projects_are_configurations_of_one_case():
    records = registry.parse_file(PLAYWRIGHT_JSON)
    added = [r for r in records if r.name == "adds an item"]
    assert sorted(r.configuration for r in added) == ["chromium", "firefox"]
    assert len({r.fingerprint for r in added}) == 1
    assert {r.explicit_id for r in added} == {"SHOP-3"}
    assert added[0].path == "e2e/cart.spec.ts" and added[0].suite == "Cart"


def test_playwright_statuses():
    records = _by_name(registry.parse_file(PLAYWRIGHT_JSON))
    flaky = records[("adds an item", None, "firefox")]
    assert [a.status for a in flaky.attempts] == [Status.FAILED, Status.PASSED]
    assert flaky.attempts[0].reason == "timeout"
    assert flaky.status is Status.PASSED and flaky.flaky_in_run
    expected = records[("removes an item", None, "chromium")]
    assert (expected.status, expected.reason) == (Status.PASSED, "expected_failure")
    assert records[("removes an item", None, "firefox")].status is Status.BROKEN
    assert records[("shows totals", None, "chromium")].status is Status.SKIPPED


def test_the_registry_sniffs_the_format():
    assert registry.detect(PYTEST_XML.read_bytes()[:4096]).name == "junit"
    assert registry.detect(PLAYWRIGHT_JSON.read_bytes()[:4096]).name == "playwright"
    with pytest.raises(ReportError, match="unrecognised"):
        registry.detect(b"name,status\nx,passed\n")


def test_a_named_format_skips_sniffing_and_an_unknown_one_is_refused():
    assert len(registry.parse_file(PYTEST_XML, format="junit")) == 9 - 1  # one rerun pair
    with pytest.raises(ReportError, match="unknown format"):
        registry.parse_file(PYTEST_XML, format="jmeter")


def test_malformed_input_is_a_report_error():
    with pytest.raises(ReportError):
        list(registry.parse(io.BytesIO(b"<testsuite><testcase"), format="junit"))
    with pytest.raises(ReportError):
        list(registry.parse(io.BytesIO(b'{"suites": '), format="playwright"))


def test_the_built_in_importers_are_registered_entry_points():
    names = {ep.name for ep in entry_points(group="aurseal.importers")}
    assert {"junit", "playwright"} <= names
    assert set(registry.importers()) >= {"junit", "playwright"}


def test_status_counts_of_the_fixtures():
    counts = Counter(r.status for r in registry.parse_file(PYTEST_XML))
    assert counts == {Status.PASSED: 4, Status.FAILED: 1, Status.BROKEN: 1, Status.SKIPPED: 2}
