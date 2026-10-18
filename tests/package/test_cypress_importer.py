"""Cypress importer: mochawesome JSON and the Module API results object."""

from __future__ import annotations

import io
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from testkeel.importers import ReportError, registry
from testkeel.model.identity import fingerprint
from testkeel.model.v1 import Status

FIXTURES = Path(__file__).parent / "fixtures"
MOCHAWESOME = FIXTURES / "cypress-mochawesome.json"
MODULE_API = FIXTURES / "cypress-run.json"


def _by_name(path):
    return {(r.suite, r.name): r for r in registry.parse_file(path)}


@pytest.mark.parametrize("fixture", [MOCHAWESOME, MODULE_API], ids=lambda p: p.name)
def test_both_formats_are_sniffed_and_reimports_keep_identities(fixture):
    assert registry.detect(fixture.read_bytes()[:4096]).name == "cypress"
    first = [r.identity for r in registry.parse_file(fixture)]
    assert len(first) == len(set(first))
    for _ in range(19):
        assert [r.identity for r in registry.parse_file(fixture)] == first


def test_mochawesome_suites_paths_and_explicit_ids():
    records = _by_name(MOCHAWESOME)
    added = records[("Cart", "adds an item")]
    assert (added.framework, added.path, added.explicit_id) == (
        "cypress",
        "cypress/e2e/cart.cy.js",
        "SHOP-3",
    )
    total = records[("Cart › Totals", "shows the total")]
    assert total.fingerprint == fingerprint(
        "cypress", "cypress/e2e/cart.cy.js", ["Cart", "Totals"], "shows the total"
    )


def test_mochawesome_statuses_and_retry_counts():
    records = _by_name(MOCHAWESOME)
    retried = records[("Cart", "removes an item")]
    assert [a.status for a in retried.attempts] == [Status.FAILED, Status.FAILED, Status.PASSED]
    assert retried.flaky_in_run and retried.duration_ms == 640
    timeout = records[("Cart › Totals", "shows the total")]
    assert (timeout.status, timeout.reason, timeout.message) == (
        Status.FAILED,
        "timeout",
        "Timed out retrying after 4000ms",
    )
    assert len(timeout.attempts) == 2 and not timeout.flaky_in_run
    pending = records[("Cart", "applies a coupon")]
    assert (pending.status, pending.reason) == (Status.SKIPPED, None)
    hook = records[("Checkout", '"before each" hook')]
    assert (hook.status, hook.message) == (Status.BROKEN, "cy.visit() failed: 503")
    not_run = records[("Checkout", "pays by card")]
    assert (not_run.status, not_run.reason) == (Status.SKIPPED, "not_run")
    assert records[("Checkout", "rejects an expired card")].status is Status.FAILED


def test_module_api_attempts_configuration_and_messages():
    records = _by_name(MODULE_API)
    assert {r.configuration for r in records.values()} == {"chrome"}
    retried = records[("Cart", "removes an item")]
    assert [a.status for a in retried.attempts] == [Status.FAILED, Status.PASSED]
    assert retried.flaky_in_run and retried.duration_ms == 640
    failed = records[("Cart › Totals", "shows the total")]
    assert failed.status is Status.FAILED and len(failed.attempts) == 2
    assert failed.message == "AssertionError: Timed out retrying after 4000ms"
    assert records[("Cart", "applies a coupon")].status is Status.SKIPPED
    assert records[("Cart", "adds an item")].explicit_id == "SHOP-3"


def test_both_formats_give_one_test_the_same_case_identity():
    mocha = _by_name(MOCHAWESOME)
    module = _by_name(MODULE_API)
    for key in module:
        assert mocha[key].fingerprint == module[key].fingerprint


def test_a_run_cypress_could_not_start_is_refused():
    body = b'{"status": "failed", "failures": 1, "message": "no browser found"}'
    with pytest.raises(ReportError, match="no browser found"):
        list(registry.parse(io.BytesIO(body), format="cypress"))


def test_malformed_and_unknown_input_is_a_report_error():
    with pytest.raises(ReportError):
        list(registry.parse(io.BytesIO(b'{"runs": '), format="cypress"))
    with pytest.raises(ReportError, match="no 'runs'"):
        list(registry.parse(io.BytesIO(b'{"stats": {}}'), format="cypress"))
    body = (
        b'{"runs": [{"spec": {"relative": "a.cy.js"},'
        b' "tests": [{"title": ["x"], "state": "odd"}]}]}'
    )
    with pytest.raises(ReportError, match="unknown Cypress test state"):
        list(registry.parse(io.BytesIO(body), format="cypress"))


def test_the_importer_is_a_registered_entry_point():
    assert "cypress" in {ep.name for ep in entry_points(group="testkeel.importers")}
