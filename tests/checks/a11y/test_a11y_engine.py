"""The accessibility engine offline: policy, parsing a canned axe result, judging, recording.

No browser and no axe here: a fake page answers ``evaluate`` with a canned axe result of
the shape axe-core 4.x returns.
"""

from __future__ import annotations

import json

import pytest

from checks._sink import MemorySink
from checks.a11y.axe import A11yReport, AxeUnavailable, axe_source, parse_results, scan
from checks.a11y.judge import judge, record, subject_for
from checks.a11y.policy import A11yPolicy, impact_rank
from verification.known_defects_manifest import parse_manifest


def _rule(rule_id: str, impact: str | None = "serious", targets=("#x",)) -> dict:
    return {
        "id": rule_id,
        "impact": impact,
        "tags": ["cat.forms", "wcag2a", "wcag412"],
        "help": f"help for {rule_id}",
        "nodes": [{"target": [t]} for t in targets],
    }


def _axe(violations=(), passes=("document-title", "html-has-lang")) -> dict:
    return {
        "testEngine": {"name": "axe-core", "version": "4.12.1"},
        "violations": list(violations),
        "passes": [{"id": p} for p in passes],
        "incomplete": [],
        "inapplicable": [{"id": "video-caption"}],
    }


class FakePage:
    url = "http://127.0.0.1:9/ui/things"

    def __init__(self, result: object = None, raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple] = []

    def evaluate(self, expression, arg=None):  # noqa: ANN001
        self.calls.append((expression, arg))
        if self.raises is not None:
            raise self.raises
        return None if len(self.calls) == 1 else self.result


class TestPolicy:
    def test_impact_ordering(self):
        assert impact_rank("minor") < impact_rank("moderate") < impact_rank("serious")
        assert impact_rank("serious") < impact_rank("critical")

    def test_missing_impact_is_treated_as_most_severe(self):
        assert A11yPolicy(min_impact="critical").is_blocking(None, "x")

    @pytest.mark.parametrize(
        ("impact", "blocks"),
        [("minor", False), ("moderate", False), ("serious", True), ("critical", True)],
    )
    def test_default_threshold_is_serious(self, impact, blocks):
        assert A11yPolicy().is_blocking(impact, "label") is blocks

    def test_disabled_rule_never_blocks_and_is_turned_off_in_axe(self):
        policy = A11yPolicy(disabled_rules=("color-contrast",))
        assert not policy.is_blocking("critical", "color-contrast")
        assert policy.axe_options()["rules"] == {"color-contrast": {"enabled": False}}

    def test_tags_drive_run_only(self):
        assert A11yPolicy(tags=("wcag21aa",)).axe_options()["runOnly"] == {
            "type": "tag",
            "values": ["wcag21aa"],
        }

    def test_invalid_policy(self):
        with pytest.raises(ValueError):
            A11yPolicy(min_impact="severe")
        with pytest.raises(ValueError):
            A11yPolicy(tags=())


class TestScanOffline:
    def test_violations_are_parsed(self):
        page = FakePage(_axe([_rule("label", targets=("#a", "#b")), _rule("region", "moderate")]))
        report = scan(page, source="/* axe */")
        assert [v.rule_id for v in report.violations] == ["label", "region"]
        assert report.violations[0].nodes == 2
        assert report.violations[0].wcag_tags == ("wcag2a", "wcag412")
        assert report.rules_run == 5 and report.axe_version == "4.12.1"
        assert [v.rule_id for v in report.blocking(A11yPolicy())] == ["label"]

    def test_the_script_is_injected_then_run_with_the_policy(self):
        page = FakePage(_axe())
        scan(page, A11yPolicy(tags=("wcag2a",)), include=["main"], exclude=["#ad"], source="S")
        assert page.calls[0] == ("S", None)
        context, options = page.calls[1][1]
        assert context == {"include": [["main"]], "exclude": [["#ad"]]}
        assert options["runOnly"]["values"] == ["wcag2a"]

    def test_a_browser_side_error_is_not_a_clean_scan(self):
        report = scan(FakePage(raises=RuntimeError("csp blocked")), source="S")
        assert not report.scanned and "csp blocked" in report.error

    @pytest.mark.parametrize("raw", [None, {}, {"violations": "x"}, []])
    def test_garbage_is_not_a_clean_scan(self, raw):
        assert not parse_results("/r", raw).scanned

    def test_empty_result_lists_mean_axe_did_not_run(self):
        report = parse_results("/r", _axe(passes=()) | {"inapplicable": []})
        assert report.rules_run == 0 and not report.scanned

    def test_no_axe_available_raises(self, monkeypatch):
        import builtins

        monkeypatch.delenv("AXE_SCRIPT_PATH", raising=False)
        real_import = builtins.__import__

        def _no_axe(name, *a, **k):  # noqa: ANN001, ANN002, ANN003
            if name.startswith("axe_playwright_python"):
                raise ImportError(name)
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_axe)
        with pytest.raises(AxeUnavailable):
            axe_source()

    def test_an_explicit_script_path_is_used(self, tmp_path, monkeypatch):
        script = tmp_path / "axe.min.js"
        script.write_text("/* vendored */", encoding="utf-8")
        monkeypatch.setenv("AXE_SCRIPT_PATH", str(script))
        assert axe_source() == "/* vendored */"
        monkeypatch.setenv("AXE_SCRIPT_PATH", str(tmp_path / "missing.js"))
        with pytest.raises(AxeUnavailable):
            axe_source()


def _report(route: str, *rules: dict, passes=("document-title", "label", "image-alt")):
    return parse_results(route, _axe(rules, passes=passes))


MANIFEST = parse_manifest(
    [
        {
            "template": "/ui/list",
            "open_issues": [
                {
                    "issue": "BUG-171",
                    "summary": "a11y",
                    "notes": "filter has no accessible name",
                    "seen_in": "rule label on the filter input",
                    "finding": "label",
                }
            ],
        }
    ]
)


class TestJudge:
    def test_a_blocking_violation_fails(self):
        out = judge([_report("http://h/ui/a", _rule("label"))], record_verdicts=False)
        assert out.status == "fail" and "label" in out.reason

    def test_only_minor_violations_pass(self):
        out = judge([_report("http://h/ui/a", _rule("region", "minor"))], record_verdicts=False)
        assert out.status == "pass"
        assert out.findings[0]["reported"] == ["region"]

    def test_an_unscanned_route_makes_the_whole_run_unknown(self):
        reports = [
            _report("http://h/ui/a"),
            A11yReport(route="http://h/ui/b", error="answered 500"),
        ]
        assert judge(reports, record_verdicts=False).status == "unknown"

    def test_no_reports_is_unknown(self):
        assert judge([], record_verdicts=False).status == "unknown"

    def test_a_known_violation_is_excused_on_its_own_route(self):
        out = judge(
            [_report("http://h/ui/list", _rule("label"))], manifest=MANIFEST, record_verdicts=False
        )
        assert out.status == "pass"
        assert out.findings[0]["open_issues"] == [{"issue": "BUG-171", "verdict": "reproduces"}]

    def test_the_same_rule_on_another_route_is_not_excused(self):
        out = judge(
            [_report("http://h/ui/other", _rule("label"))], manifest=MANIFEST, record_verdicts=False
        )
        assert out.status == "fail"

    def test_another_rule_on_the_known_route_is_not_excused(self):
        out = judge(
            [_report("http://h/ui/list", _rule("label"), _rule("image-alt", "critical"))],
            manifest=MANIFEST,
            record_verdicts=False,
        )
        assert out.status == "fail" and "image-alt" in out.reason and "BUG-171" not in out.reason

    def test_a_known_violation_that_stopped_reproducing_fails_as_cleared(self):
        out = judge([_report("http://h/ui/list")], manifest=MANIFEST, record_verdicts=False)
        assert out.status == "fail"
        assert "KNOWN DEFECT CLEARED BUG-171" in out.reason

    def test_a_rule_that_did_not_run_cannot_clear(self):
        """Silence from an observer that could not see the rule is not a fix."""
        report = _report("http://h/ui/list", passes=("document-title",))
        out = judge([report], manifest=MANIFEST, record_verdicts=False)
        assert out.status == "pass"
        assert out.findings[0]["open_issues"] == []

    def test_subject_is_the_path(self):
        assert subject_for("http://127.0.0.1:8765/ui/list") == "/ui/list"
        assert subject_for("http://h/ui/list?tab=open") == "/ui/list?tab=open"
        assert subject_for("/ui/list") == "/ui/list"


class TestRecord:
    def test_zero_violation_scan_still_leaves_a_row(self):
        sink = MemorySink()
        record([_report("http://h/ui/a")], sink)
        assert sink.rows["metric_a11y_scan"][0]["violations"] == 0
        assert "metric_a11y_violation" not in sink.rows

    def test_violation_rows_carry_the_known_defect(self):
        sink = MemorySink()
        record([_report("http://h/ui/list", _rule("label"))], sink, MANIFEST)
        row = sink.rows["metric_a11y_violation"][0]
        assert (row["route"], row["rule_id"], row["known_defect"]) == (
            "/ui/list",
            "label",
            "BUG-171",
        )
        assert json.dumps(row)

    def test_unscanned_routes_record_nothing(self):
        sink = MemorySink()
        record([A11yReport(route="http://h/x", error="unreachable")], sink)
        assert sink.rows == {}
