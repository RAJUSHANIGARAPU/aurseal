"""Tests for shape, parties, loader, fixture_contract and the case manifest."""

from __future__ import annotations

import json

import pytest

from test_data import fixture_contract as fc
from test_data import loader
from test_data.parties import AmbiguousMatch, LiveResolver, NothingSelectable, select_unique
from test_data.scenario_manifest import CaseManifest, CaseState, ManifestError
from test_data.shape import ObservedShape, ShapeNotObserved

pytest_plugins = ["pytester"]


class TestObservedShape:
    BODY = {"billing": {"months": 3, "method": ""}, "lines": [{"gross": 10}, {"gross": 5}]}

    def test_unobserved_stays_none_never_a_default(self):
        shape = ObservedShape.observe(
            "order o-1",
            self.BODY,
            {
                "term_months": "billing.months",
                "method": "billing.method",
                "currency": "billing.currency",
                "line_count": lambda b: len(b["lines"]),
                "first_gross": "lines.0.gross",
            },
        )
        assert shape.get("term_months") == 3 and shape.get("line_count") == 2
        assert shape.get("method") is None and not shape.observed("currency")
        assert shape.describe() == (
            "order o-1 [term_months=3, method=?, currency=?, line_count=2, first_gross=10]"
        )

    def test_require_turns_a_gap_into_a_precondition_failure(self):
        shape = ObservedShape.observe("o", self.BODY, {"m": "billing.months", "c": "x.y"})
        assert shape.require("m") is shape
        with pytest.raises(ShapeNotObserved, match=r"\['c'\]"):
            shape.require("m", "c")
        assert ObservedShape.unknown().describe() == "unknown [nothing observed]"


RECORDS = [
    {"id": "1", "name": "Acme", "state": "active"},
    {"id": "2", "name": "Acme", "state": "archived"},
    {"id": "3", "name": "Test Beta", "state": "active"},
    {"id": "4", "name": "", "state": "active"},
]


def _active(r):
    return r["state"] == "active"


class TestPartySelection:
    def test_unique_preferred_name_wins_and_unselectable_duplicates_are_ignored(self):
        assert select_unique(RECORDS, "supplier", "Acme", selectable=_active)["id"] == "1"

    def test_two_selectable_records_with_the_name_fail_loudly(self):
        with pytest.raises(AmbiguousMatch, match=r"2 selectable supplier records.*\['1', '2'\]"):
            select_unique(RECORDS, "supplier", "Acme")

    def test_ambiguity_is_judged_only_on_the_requested_name(self):
        dupes = RECORDS + [{"id": "5", "name": "Test Beta", "state": "active"}]
        assert select_unique(dupes, "supplier", "Acme", selectable=_active)["id"] == "1"

    def test_drifted_name_falls_back_preferring_the_prefix_never_blank(self):
        picked = select_unique(
            RECORDS, "supplier", "Gone", selectable=_active, fallback_prefix="Test "
        )
        assert picked["id"] == "3"
        only_blank = [{"id": "9", "name": " ", "state": "active"}]
        with pytest.raises(NothingSelectable, match="seeded"):
            select_unique(only_blank, "supplier", None)

    def test_resolver_caches_per_kind_and_name(self):
        calls = []
        resolver = LiveResolver(lambda kind: calls.append(kind) or RECORDS, selectable=_active)
        assert resolver.resolve("supplier", "Acme")["id"] == "1"
        resolver.resolve("supplier", "Acme")
        assert calls == ["supplier"]


class TestLoader:
    def _write(self, d, name, data):
        (d / name).write_text(json.dumps(data))

    def test_overlay_deep_merges_and_get_fails_naming_both_files(self, tmp_path):
        self._write(
            tmp_path, "checkout.json", {"user": {"name": "A", "tier": "basic"}, "skus": [1, 2]}
        )
        self._write(tmp_path, "checkout.staging.json", {"user": {"tier": "gold"}, "skus": [9]})
        data = loader.load("checkout", "staging", tmp_path)
        assert data.get("user") == {"name": "A", "tier": "gold"} and data.get("skus") == [9]
        assert data.get("user.missing", "dflt") == "dflt"
        with pytest.raises(KeyError, match=r"checkout.json \+ checkout.staging.json"):
            data.get("user.missing")
        data.get("user")["name"] = "mutated"
        assert data.get("user.name") == "A", "callers get copies, the cache is not mutable"

    def test_env_defaults_and_missing_overlay(self, tmp_path, monkeypatch):
        self._write(tmp_path, "d.json", {"a": 1})
        monkeypatch.setenv("TEST_ENV", "nowhere")
        data = loader.load("d", directory=tmp_path)
        assert data.get("a") == 1 and data.overlay_file is None
        with pytest.raises(KeyError, match="no overlay"):
            data.get("b")
        with pytest.raises(FileNotFoundError):
            loader.load("absent", directory=tmp_path)

    def test_the_repo_test_data_file_loads(self):
        assert loader.load("test_data", "qa").get("valid_search")


CLIENTS = {
    "client-postal": {
        "name": "Client ${SEED_TAG}",
        "delivery": "post",
        "addresses": [{"purpose": "postal", "primary": True}, {"purpose": "visit"}],
    },
    "client-email": {
        "name": "Mail ${SEED_TAG}",
        "delivery": "email",
        "addresses": [{"purpose": "visit", "primary": True}],
    },
}

RULES = [
    fc.Required("name"),
    fc.CarriesRunTag("name"),
    fc.When(
        "delivery",
        "post",
        "addresses[].purpose",
        lambda purposes: "postal" in purposes,
        "no postal address to deliver to",
    ),
    fc.AtMost("addresses[].primary", 1, bool),
]


class TestFixtureContract:
    def test_good_fixtures_pass(self):
        assert fc.check(CLIENTS, RULES) == [] and fc.unique_across(CLIENTS, "name") == []

    def test_each_rule_bites(self):
        bad = {
            "x": {
                "name": "Fixed name",
                "delivery": "post",
                "addresses": [{"purpose": "visit", "primary": True}, {"primary": True}],
            },
            "y": {"name": "", "delivery": "email"},
        }
        rules = {str(v).split("]")[0] for v in fc.check(bad, RULES)}
        assert rules == {
            "x: [run-tag",
            "x: [conditional",
            "x: [at-most",
            "y: [required",
            "y: [run-tag",
        }

    def test_unique_across_fixtures(self):
        dup = {"a": {"code": "Z1"}, "b": {"code": "Z1"}, "c": {"code": "Z2"}}
        (v,) = fc.unique_across(dup, "code")
        assert v.fixture == "a, b"

    def test_values_at_fans_out(self):
        doc = {"a": [{"b": [{"c": 1}, {"c": 2}]}, {"b": [{"c": 3}]}, {"x": 1}]}
        assert fc.values_at(doc, "a[].b[].c") == [1, 2, 3]
        assert fc.values_at(doc, "missing.path") == []


MANIFEST = {
    "suite": "storage-changes",
    "cases": [
        {
            "id": "ST-QUOTA-1",
            "title": "double the quota",
            "state": "automated",
            "test": "t::a",
            "seen_passing": True,
            "proof": "run 12",
            "inputs": {"quota_gb": 200},
        },
        {"id": "ST-TIER-1", "title": "archive tier", "state": "planned", "waiting_on": "tier lock"},
        {"id": "ST-HOLD-1", "title": "legal hold", "state": "manual", "why_manual": "no way in"},
        {"id": "ST-QUOTA-2", "title": "halve the quota", "state": "automated", "test": "t::b"},
    ],
}


class TestCaseManifest:
    def test_summary_counts_only_seen_passing_automated_as_proven(self):
        m = CaseManifest.from_dict(MANIFEST)
        assert m.summary() == {"automated": 2, "planned": 1, "manual": 1, "proven": 1}

    @pytest.mark.parametrize(
        "row,needle",
        [
            ({"id": "X", "title": "t", "state": "maybe"}, "unknown state"),
            ({"id": "X", "title": "t", "state": "automated"}, "name its test"),
            ({"id": "X", "title": "t", "state": "manual"}, "why_manual"),
            ({"id": "X", "title": "t", "state": "planned"}, "waiting_on"),
            (
                {
                    "id": "X",
                    "title": "t",
                    "state": "planned",
                    "waiting_on": "w",
                    "seen_passing": True,
                },
                "proof",
            ),
            ({"title": "t", "state": "planned", "waiting_on": "w"}, "required"),
        ],
    )
    def test_malformed_cases_fail(self, row, needle):
        with pytest.raises(ManifestError, match=needle):
            CaseManifest.from_dict({"cases": [row]})

    def test_duplicate_empty_and_another_layout(self):
        row = {"id": "X", "title": "t", "state": "planned", "waiting_on": "w"}
        with pytest.raises(ManifestError, match="duplicate"):
            CaseManifest.from_dict({"cases": [row, row]})
        with pytest.raises(ManifestError, match="no cases"):
            CaseManifest.from_dict({"cases": []})
        with pytest.raises(ManifestError, match="no cases"):
            CaseManifest.from_dict({"rows": [row]})  # one schema: other keys are not read

    def test_cases_become_parametrized_tests_with_gaps_skipped(self, pytester, tmp_path):
        path = tmp_path / "m.json"
        path.write_text(json.dumps(MANIFEST))
        pytester.makepyfile(
            f"""
            import pytest
            from test_data.scenario_manifest import CaseManifest

            M = CaseManifest.load({str(path)!r})

            @pytest.mark.parametrize("case", M.params())
            def test_case(case):
                assert case.title
            """
        )
        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=2, skipped=2)
        result.stdout.fnmatch_lines(["*test_case?ST-QUOTA-1?*PASSED*"])
        automated = CaseManifest.from_dict(MANIFEST).params(CaseState.AUTOMATED)
        assert [p.id for p in automated] == ["ST-QUOTA-1", "ST-QUOTA-2"]
