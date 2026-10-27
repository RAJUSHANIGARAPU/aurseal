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


# ---------------------------------------------------------------------- element scope


def _node(target, hosts=(), text=None, fg=None):
    node = {
        "target": [target],
        "html": f"<span>{text}</span>",
        "_hosts": list(hosts),
        "_text": text,
    }
    if fg:
        node["any"] = [{"id": "color-contrast", "data": {"fgColor": fg}}]
    return node


def _contrast(violations=(), passes=()):
    raw = _axe(passes=())
    raw["violations"] = (
        [{**_rule("color-contrast"), "nodes": list(violations)}] if violations else []
    )
    raw["passes"] = [{"id": "color-contrast", "nodes": list(passes)}, {"id": "document-title"}]
    return raw


SCOPED = [
    {
        "template": "/ui/things",
        "open_issues": [
            {
                "issue": "BUG-301",
                "summary": "a11y",
                "seen_in": "pager label",
                "finding": "color-contrast",
                "host": "app-pager",
                "text": "Next page",
                "fg_color": "#999999",
            }
        ],
    }
]


class TestElementScope:
    def test_nodes_keep_target_text_and_measured_colour(self):
        report = parse_results(
            "/r", _contrast([_node("#n", ["app-pager"], "Next page", "#999999")])
        )
        node = report.nodes[0]
        assert (node.outcome, node.target, node.text, node.fg_color, node.hosts) == (
            "violation",
            "#n",
            "Next page",
            "#999999",
            ("app-pager",),
        )

    def test_passes_are_requested_from_axe(self):
        assert A11yPolicy().axe_options()["resultTypes"] == ["violations", "passes"]

    def test_hosts_are_passed_to_the_page_only_when_named(self):
        page = FakePage(_axe())
        scan(page, source="S", hosts=["app-pager"])
        assert page.calls[1][1][2] == ["app-pager"]

    def test_the_matching_element_reproduces_and_another_element_is_unexplained(self):
        manifest = parse_manifest(SCOPED)
        own = _node("#n", ["app-pager"], "Next page", "#999999")
        other = _node("#m", ["app-footer"], "Imprint", "#999999")
        ok = judge(
            [parse_results("/ui/things", _contrast([own]))],
            manifest=manifest,
            record_verdicts=False,
        )
        assert ok.status == "pass"
        assert ok.findings[0]["open_issues"] == [{"issue": "BUG-301", "verdict": "reproduces"}]
        mixed = judge(
            [parse_results("/ui/things", _contrast([own, other]))],
            manifest=manifest,
            record_verdicts=False,
        )
        assert mixed.status != "pass" and "color-contrast" in mixed.reason

    def test_a_matching_pass_node_clears(self):
        report = parse_results(
            "/ui/things", _contrast(passes=[_node("#n", ["app-pager"], "Next page", "#999999")])
        )
        outcome = judge([report], manifest=parse_manifest(SCOPED), record_verdicts=False)
        assert outcome.status != "pass" and "KNOWN DEFECT CLEARED BUG-301" in outcome.reason

    def test_a_missing_element_is_held_empty_never_cleared(self):
        report = parse_results(
            "/ui/things", _contrast(passes=[_node("#z", ["app-footer"], "Imprint", "#000000")])
        )
        outcome = judge([report], manifest=parse_manifest(SCOPED), record_verdicts=False)
        assert outcome.status == "pass"
        assert outcome.findings[0]["open_issues"] == [{"issue": "BUG-301", "verdict": "held_empty"}]

    def test_manifest_rails(self):
        from verification.known_defects_manifest import check_manifest

        loose = [
            {
                **SCOPED[0],
                "open_issues": [{**SCOPED[0]["open_issues"][0], "text": None, "fg_color": None}],
            }
        ]
        assert any(
            "needs a host plus text or fg_color" in p for p in check_manifest(parse_manifest(loose))
        )
        hostless = [{**SCOPED[0], "open_issues": [{**SCOPED[0]["open_issues"][0], "host": None}]}]
        assert any("needs a host" in p for p in check_manifest(parse_manifest(hostless)))
        many = [
            {"template": f"/r{i}", "open_issues": [{"issue": f"BUG-{i}", "summary": "s"}]}
            for i in range(16)
        ]
        assert any("exceed the cap of 15" in p for p in check_manifest(parse_manifest(many)))
        assert check_manifest(parse_manifest(many), max_issues=None) == []


# ---------------------------------------------------------------------- instrument control


class ControlPage(FakePage):
    def __init__(self, result, url="http://127.0.0.1:9/ui/things"):
        super().__init__(result)
        self.url = url
        self.content = None

    def set_content(self, html):
        self.content = html

    def evaluate(self, expression, arg=None):  # noqa: ANN001
        self.calls.append((expression, arg))
        return None if expression == "S" else self.result


class TestInstrumentControl:
    def test_a_scanner_that_reports_nothing_does_not_bite(self):
        from checks.a11y.runner import ScannerDoesNotBite, instrument_control

        page = ControlPage(_axe())  # a fake axe that never reports a violation
        with pytest.raises(ScannerDoesNotBite, match="scanner does not bite"):
            instrument_control(page, source="S")
        assert "<img" in page.content and "alt" not in page.content

    def test_a_scanner_that_reports_image_alt_passes(self):
        from checks.a11y.runner import instrument_control

        report = instrument_control(ControlPage(_axe([_rule("image-alt", "critical")])), source="S")
        assert [v.rule_id for v in report.violations] == ["image-alt"]

    def test_a_redirect_makes_the_route_not_scanned(self, monkeypatch):
        import contextlib

        from checks.a11y import runner

        page = ControlPage(
            _axe([_rule("image-alt", "critical")]), url="http://h.example.test/login"
        )

        class Context:
            def new_page(self):
                return page

        page.goto = lambda route, **kw: None
        monkeypatch.setattr(runner, "axe_source", lambda: "S")
        monkeypatch.setattr(
            runner, "browser_context", lambda *a, **k: contextlib.nullcontext(Context())
        )
        reports = runner.scan_routes(["http://h.example.test/ui/things"])
        assert not reports[0].scanned
        assert "not_scanned" in reports[0].error and "/login" in reports[0].error
        assert runner.landed_elsewhere("http://h/ui/a?x=1", "http://h/ui/a#top") is False
