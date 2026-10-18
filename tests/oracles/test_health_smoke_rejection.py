"""Tests for data_health, smoke, rejection and reporting."""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
import requests

from api.clients.http_engine import ApiResponse, HttpEngine
from oracles import data_health as dh
from oracles import reporting, smoke
from oracles.rejection import Bound, ReadBack, RejectionSignals, Signal, appeared_since
from tests.api_layer.fakes import FakeAdapter, make_response, session_with


def uuid7(at: datetime) -> str:
    ms = int(at.timestamp() * 1000)
    value = (ms << 80) | (0x7 << 76) | (0x2 << 62) | 0x1234
    return str(uuid.UUID(int=value))


NOW = datetime(2030, 6, 1, tzinfo=timezone.utc)

RECORDS = [
    {
        "id": "r1",
        "start": "2030-01-01T00:00:00.000+01:00",
        "end": "2030-12-31T00:00:00.000Z",
        "status": "OK",
        "kind": "REFUND",
        "payee": {"account": "x"},
        "draft": uuid7(NOW),
    },
    {
        "id": "r2",
        "start": "2030-01-01T00:00:00+01:00",
        "end": "2030-12-31",
        "status": "FAILED",
        "kind": "REFUND",
        "payee": {"account": ""},
        "draft": uuid7(NOW - timedelta(days=10)),
    },
]

RULES = [
    dh.datetime_precision(["start", "end"]),
    dh.date_without_time(["start", "end"]),
    dh.status_in("status", {"FAILED"}, "failed-status"),
    dh.required_when("kind", "REFUND", ["payee.account"], "refund-payee"),
    dh.stale_by_uuid7("draft", timedelta(days=2), now=NOW),
]


class TestAnalyzer:
    def test_clean_record_has_no_findings_and_each_rule_bites_once(self):
        findings = dh.analyze(RECORDS, RULES, lambda r: r["id"])
        assert {f.record_id for f in findings} == {"r2"}
        assert sorted(f.rule for f in findings) == sorted(r.id for r in RULES)

    def test_uuid7_time(self):
        assert dh.uuid7_time(uuid7(NOW)) == NOW
        assert dh.uuid7_time(str(uuid.uuid4())) is None and dh.uuid7_time("nope") is None

    def test_rule_selection(self):
        assert dh.select_rules(RULES, "all") == RULES
        assert [r.id for r in dh.select_rules(RULES, "stale, failed-status")] == [
            "stale",
            "failed-status",
        ]
        with pytest.raises(ValueError, match="unknown rule"):
            dh.select_rules(RULES, "stale,typo")


class TestScanner:
    def _api(self, pages: list[list[dict]], fail_at: int | None = None):
        def get(path, params):
            page = params["page"]
            if page == fail_at:
                return ApiResponse(403)
            body = {"items": pages[page] if page < len(pages) else []}
            return ApiResponse(200, __import__("json").dumps(body).encode())

        return dh.GetOnlyApi(get)

    def test_pages_until_a_short_page(self):
        api = self._api([[RECORDS[0], RECORDS[0]], [RECORDS[1]]])
        report = dh.Scanner(api, RULES, page_size=2).scan("/records")
        assert report.scanned == 3 and report.complete and len(report.findings) == len(RULES)

    def test_cap_is_reported_as_incomplete(self):
        api = self._api([[RECORDS[0]] * 2] * 5)
        report = dh.Scanner(api, RULES, page_size=2, max_pages=2).scan("/records")
        assert not report.complete and "capped" in report.describe()

    def test_refused_read_is_reported_not_swallowed(self):
        report = dh.Scanner(self._api([[RECORDS[0]] * 2], fail_at=1), RULES, page_size=2).scan("/r")
        assert report.scanned == 2 and "HTTP 403" in report.notes[0]

    def test_read_only_api_exposes_get_and_nothing_else(self):
        adapter = FakeAdapter().on("GET", "/x", make_response(200, {"ok": 1}))
        api = dh.GetOnlyApi.over(HttpEngine("https://h/", session=session_with(adapter)))
        assert api.get("x").json() == {"ok": 1}
        assert [n for n in dir(api) if not n.startswith("_")] == ["get", "over"]
        with pytest.raises(AttributeError):
            api.post  # noqa: B018

    @pytest.mark.parametrize("module", [dh, smoke])
    def test_scan_and_smoke_code_never_calls_a_writing_verb(self, module):
        tree = ast.parse(inspect.getsource(module))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not calls & {"post", "put", "patch", "delete", "request", "post_multipart"}


class TestSmoke:
    def _api(self, routes):
        def get(path, params):
            outcome = routes[path]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return dh.GetOnlyApi(get)

    def test_checks_assert_and_never_abstain(self):
        api = self._api(
            {
                "/version": ApiResponse(200, b'{"version": "1.2.3"}'),
                "/items": ApiResponse(200, b'{"content": []}'),
                "/blank": ApiResponse(200, b'{"version": ""}'),
                "/down": requests.exceptions.ConnectionError("refused"),
                "/err": ApiResponse(503),
            }
        )
        checks = [
            smoke.SmokeCheck(
                "version", "/version", (smoke.status_is(), smoke.json_path_non_blank("version"))
            ),
            smoke.SmokeCheck("listing", "/items", (smoke.json_path_is_list("content"),)),
            smoke.SmokeCheck("blank", "/blank", (smoke.json_path_non_blank("version"),)),
            smoke.SmokeCheck("down", "/down"),
            smoke.SmokeCheck("err", "/err"),
        ]
        results = {r.name: r for r in smoke.run_smoke(api, checks)}
        assert results["version"].ok and results["listing"].ok
        assert not results["blank"].ok and "unreachable" in results["down"].problems[0]
        assert "HTTP 200" in results["err"].problems[0]
        assert "FAIL down" in smoke.describe(results.values())

    def test_list_expectations(self):
        r = ApiResponse(200, b'{"content": [1], "x": 1}')
        assert smoke.json_path_is_list("content", 2)(r) and smoke.json_path_is_list("x")(r)
        assert smoke.json_path_is_list("missing")(r) == ["missing is missing"]
        assert smoke.json_path_is_list("")(ApiResponse(200, b"[1]")) == []


CAP = Bound.at_most("100")


class TestRejection:
    def test_nothing_fired_means_accepted(self):
        s = RejectionSignals("150", CAP, "150")
        assert s.entered_is_out_of_bound() and not s.rejected
        assert s.read_back_state is ReadBack.BEYOND_LIMIT and s.describe().startswith("ACCEPTED")

    @pytest.mark.parametrize(
        "kwargs,signal",
        [
            ({"api_status": 422}, Signal.API_STATUS),
            ({"inline_errors": ["Too high"]}, Signal.FIELD_MESSAGE),
            ({"control_invalid": True}, Signal.CONTROL_FLAGGED),
            ({"page_alerts": ["Not saved"]}, Signal.BANNER_MESSAGE),
            ({"read_back": "100,00"}, Signal.VALUE_ADJUSTED),
            ({"state_changed": False}, Signal.STATE_UNCHANGED),
        ],
    )
    def test_each_surface_counts(self, kwargs, signal):
        s = RejectionSignals(**{"entered": "150", "allowed": CAP, "read_back": "150", **kwargs})
        assert s.fired == [signal] and s.rejected and signal.value in s.describe()

    def test_in_range_entry_is_never_a_correction(self):
        s = RejectionSignals("80", CAP, "75")
        assert not s.entered_is_out_of_bound() and not s.rejected

    @pytest.mark.parametrize("read_back", ["", "   ", "n/a"])
    def test_blank_or_unparseable_read_back_is_not_a_rejection(self, read_back):
        s = RejectionSignals("150", CAP, read_back)
        assert not s.rejected
        assert s.read_back_state in (ReadBack.BLANK, ReadBack.UNPARSEABLE)

    def test_server_errors_and_successes_are_not_rejections(self):
        assert not RejectionSignals(api_status=500).rejected
        assert not RejectionSignals(api_status=200, state_changed=True).rejected
        assert RejectionSignals().read_back_state is ReadBack.NOT_READ

    def test_bounds_and_percent_display(self):
        floor = Bound.at_least("0")
        assert (
            floor.contains(D("0")) and not floor.contains(D("-0.01")) and not floor.contains(None)
        )
        assert RejectionSignals("-5", floor, "0 %").fired == [Signal.VALUE_ADJUSTED]
        assert RejectionSignals("1,250.5", CAP, "100", locale="en_US").rejected

    def test_appeared_since_is_a_multiset_difference(self):
        assert appeared_since(["Required"], ["Required", "Required", "Too high"]) == [
            "Required",
            "Too high",
        ]
        assert appeared_since(["a", ""], ["a", " "]) == []


def test_reporting_logs_intent_and_outcome(caplog):
    caplog.set_level("INFO", logger="oracles.reporting")
    with reporting.validating("refund is negative", expected="< 0"):
        pass
    with pytest.raises(AssertionError):
        with reporting.validating("total adds up"):
            raise AssertionError("no")
    reporting.skipped("tax ratio", "no tax on this product")
    reporting.note("payload", "{}")
    text = caplog.text
    assert "VALIDATING refund is negative (expected < 0)" in text and "OK refund" in text
    assert "FAILED total adds up" in text and "SKIPPED tax ratio" in text


class TestScanReportColumns:
    """Per-rule counts with a separate unverified column and a creator split."""

    @staticmethod
    def _supplier_rule(known: set[str]) -> dh.Rule:
        def check(record):
            ref = record.get("supplier")
            if ref is None:
                return []
            if ref == "unreadable":
                return [dh.Unverified("supplier could not be read")]
            return [] if ref in known else [f"supplier {ref} does not resolve"]

        return dh.Rule("dangling-supplier", "Suppliers whose reference is broken", check)

    def _report(self, sample_limit: int = 5) -> dh.ScanReport:
        records = [
            {"id": "a1", "supplier": "gone", "created_by": "Bot@Example.test"},
            {"id": "a2", "supplier": "gone", "created_by": "pat@example.test"},
            {"id": "a3", "supplier": "gone"},
            {"id": "a4", "supplier": "unreadable", "created_by": "pat@example.test"},
            {"id": "a5", "supplier": "ok", "created_by": "pat@example.test"},
        ]
        rules = [self._supplier_rule({"ok"}), dh.status_in("status", {"FAILED"})]

        def creator(record):
            return record.get("created_by")

        findings = dh.analyze(
            records,
            rules,
            lambda r: r["id"],
            created_by=creator,
            is_automation=dh.accounts_predicate(["bot@example.test"], creator),
            entity_key="orders",
        )
        return dh.ScanReport(
            findings=findings,
            scanned=5,
            requests=1,
            scanned_per_key={"orders": 5},
            rules=rules,
            automation_accounts=("bot@example.test",),
            sample_limit=sample_limit,
        )

    def test_unverified_is_never_a_finding(self):
        summary = self._report().by_rule()["dangling-supplier"]
        assert summary.count == 3 and summary.unverified == 1
        assert [f.record_id for f in summary.unverified_samples] == ["a4"]
        assert not summary.unverified_samples[0].verified

    def test_creator_split(self):
        s = self._report().by_rule()["dangling-supplier"]
        assert (s.by_automation, s.by_others, s.creator_unknown) == (1, 1, 1)
        assert s.top_creators() == [("Bot@Example.test", 1), ("pat@example.test", 1)]

    def test_every_rule_listed_even_without_findings(self):
        assert self._report().by_rule()["bad-status"].count == 0

    def test_samples_are_bounded(self):
        assert len(self._report(sample_limit=2).by_rule()["dangling-supplier"].samples) == 2

    def test_markdown_has_the_columns(self):
        md = self._report().to_markdown("qa", datetime(2031, 5, 4, tzinfo=timezone.utc))
        assert md.startswith("# Data health — qa\n")
        assert "Read-only (GET only), 1 requests." in md
        assert "| dangling-supplier | 3 | 1 | 1 | 1 | 1 |" in md
        assert "| orders | 5 |" in md
        assert "Automation accounts: bot@example.test" in md
        assert "Unverified sample (1 of 1):" in md
        assert "STOPPED" not in md

    def test_stopped_scan_says_it_is_not_a_result(self):
        report = self._report()
        report.abort("orders page 0: HTTP 503")
        report.abort("a second reason is ignored")
        assert not report.complete
        assert "**SCAN ABORTED — partial report, do not treat as a result:** orders" in (
            report.to_markdown("qa")
        )
        assert report.to_dict("qa")["abort_reason"] == "orders page 0: HTTP 503"

    def test_json_shape(self):
        data = self._report().to_dict("qa")
        rule = data["rules"]["dangling-supplier"]
        assert (rule["count"], rule["unverified"], rule["by_automation"]) == (3, 1, 1)
        assert rule["samples"][0] == {
            "entity_key": "orders",
            "record_id": "a1",
            "detail": "supplier gone does not resolve",
            "created_by": "Bot@Example.test",
            "origin": "automation",
        }

    def test_describe_counts_unverified_apart(self):
        assert (
            self._report().describe().startswith("5 record(s) scanned, 3 finding(s), 1 unverified")
        )
