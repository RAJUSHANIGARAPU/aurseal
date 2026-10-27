"""Service selection (gate.service_selection): derive, select, replay and the CI switch.

Every service, test and defect id below is invented; only the drift test reads the real map.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

from gate import service_selection as svc
from gate.run_inputs import RunInputsError, plan
from gate.service_selection import (
    Collected,
    SelectionError,
    ServiceMap,
    check,
    ci_decide,
    derive,
    derive_entry,
    is_plain,
    merge_rows,
    parse_services,
    render_markdown,
    replay,
    select,
    services_from_delta,
    strip_params,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

A = "suite/test_orders.py::test_order_total"
B = "suite/test_orders.py::test_order_refund"
C = "suite/test_mail.py::test_mail_sent"
D = "suite/test_flow.py::test_end_to_end"
E = "suite/test_misc.py::test_unrelated"


def _map(tests: dict | None = None, **overrides) -> ServiceMap:
    data = {
        "roots": ["suite"],
        "services": {
            "orders": {"names": ["order"], "fixtures": ["order_api"]},
            "mail": {"markers": ["mail"], "flows": ["notify"], "classes": ["MailCase"]},
            "edge": {"fullSuite": True},
        },
        "core": ["suite/test_flow.py"],
        "manual": {},
        "tests": tests
        if tests is not None
        else {
            A: {"services": ["orders"], "source": "inferred"},
            B: {"services": ["orders"], "source": "inferred", "excluded_by": ["slow"]},
            C: {"services": ["mail"], "source": "tag"},
            D: {"services": ["mail", "orders"], "source": "inferred"},
            E: {"services": [], "source": "inferred"},
        },
    }
    data.update(overrides)
    return ServiceMap.from_json(data)


def _c(tid: str, markers=None, fixtures=(), classes=()) -> Collected:
    return Collected(tid, dict(markers or {}), set(fixtures), set(classes))


# ------------------------------------------------------------------ collection rows


def test_parameters_are_stripped_and_cases_merged():
    assert strip_params("a/test_x.py::test_y[chromium-1]") == "a/test_x.py::test_y"
    merged = merge_rows(
        [
            {"nodeid": "a/test_x.py::test_y[1]", "markers": {"service": ["orders"]}},
            {
                "nodeid": "a/test_x.py::test_y[2]",
                "markers": {"service": ["mail"]},
                "fixtures": ["f"],
            },
        ]
    )
    assert list(merged) == ["a/test_x.py::test_y"]
    entry = merged["a/test_x.py::test_y"]
    assert entry.markers["service"] == ["orders", "mail"] and entry.fixtures == {"f"}


# ------------------------------------------------------------------ derive rules


def test_a_service_marker_is_a_tag():
    entry = derive_entry(_c(E, {"service": ["mail"]}), _map(), [])
    assert entry == {"services": ["mail"], "source": "tag"}


def test_a_service_specific_marker_is_a_tag_and_beats_inference():
    entry = derive_entry(_c(A, {"mail": []}), _map(), [])
    assert entry == {"services": ["mail"], "source": "tag"}  # the "order" name rule is ignored


@pytest.mark.parametrize(
    "collected",
    [
        _c(E, {"journey_group": ["notify"]}),  # flow marker
        _c(E, {"feature": ["notify"]}),  # flow marker
        _c(E, classes=["MailCase"]),  # base class
    ],
)
def test_flow_and_class_rules_infer_a_service(collected):
    assert derive_entry(collected, _map(), []) == {"services": ["mail"], "source": "inferred"}


def test_name_and_fixture_rules_infer_a_service():
    assert derive_entry(_c(A), _map(), [])["services"] == ["orders"]
    assert derive_entry(_c(E, fixtures=["order_api"]), _map(), [])["services"] == ["orders"]


def test_no_rule_matching_is_an_empty_service_list():
    assert derive_entry(_c(E), _map(), []) == {"services": [], "source": "inferred"}


def test_a_manual_correction_replaces_everything_derived():
    smap = _map(manual={A: ["mail"]})
    assert derive_entry(_c(A, {"service": ["orders"]}), smap, []) == {
        "services": ["mail"],
        "source": "manual",
    }


def test_markers_the_gate_excludes_are_recorded():
    entry = derive_entry(_c(A, {"slow": []}), _map(), ["unit", "slow"])
    assert entry["excluded_by"] == ["slow"]


def test_check_reports_drift_and_dead_references():
    collected = {t: _c(t) for t in (A, C, D)}
    smap = _map()
    smap.tests = derive(smap, collected)
    assert check(smap, collected) == []
    smap.tests[A] = {"services": ["mail"], "source": "manual"}
    assert "differ from derive for 1 test(s)" in check(smap, collected)[0]
    broken = _map(manual={"suite/test_gone.py::test_x": ["nowhere"]}, core=["suite/nothing"])
    broken.services["orders"] = svc.Service("orders", names=("(",))
    problems = check(broken, collected)
    assert "manual entry suite/test_gone.py::test_x names no collected test" in problems
    assert "manual entry suite/test_gone.py::test_x: unknown service 'nowhere'" in problems
    assert "core entry suite/nothing matches no collected test" in problems
    assert any("bad name pattern" in p for p in problems)


def test_derive_keeps_entries_of_files_that_collected_nothing(tmp_path):
    (tmp_path / "suite").mkdir()
    (tmp_path / "suite" / "test_extras.py").write_text("", encoding="utf-8")
    kept = {"services": ["mail"], "source": "inferred"}
    keep = {"suite/test_extras.py::test_x": kept, "suite/test_gone.py::test_y": kept}
    derived = derive(_map(), {A: _c(A)}, [], keep=keep, root=tmp_path)
    assert derived["suite/test_extras.py::test_x"] == kept
    assert "suite/test_gone.py::test_y" not in derived
    assert A in derived


def test_the_committed_map_is_what_derive_produces():
    """The generated part of config/service_map.json is regenerated, never hand-edited."""
    smap = ServiceMap.load()
    collected = svc.collect(smap.roots, ROOT)
    assert check(smap, collected) == []
    assert smap.dumps() == svc.MAP_FILE.read_text(encoding="utf-8")


# ------------------------------------------------------------------ select


def test_select_unions_core_matching_unknown_and_new_tests():
    new = "suite/test_new.py::test_added"
    selection = select(["orders"], _map(), current=[A, B, C, D, E, new])
    assert not selection.full
    assert set(selection.tests) == {A, B, D, E, new}
    assert selection.tests[D] == ["core flow", "orders (inferred)"]
    assert selection.tests[E] == ["no known service"]
    assert selection.tests[new] == ["no map entry yet (new test)"]
    assert C not in selection.tests


def test_deleted_tests_are_dropped_with_a_note():
    selection = select(["mail"], _map(), current=[C, D])
    assert set(selection.tests) == {C, D} and "name no collected test" in selection.notes[0]


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        (["orders", "billing-x"], "service billing-x is not in the map"),
        (["edge"], "service edge is flagged fullSuite"),
        ([], "no changed services"),
    ],
)
def test_unknown_full_suite_and_empty_select_everything(changed, reason):
    selection = select(changed, _map())
    assert selection.full and reason in selection.full_reasons[0]
    assert selection.runner() == ""  # EMPTY means the full suite


def test_allow_empty_keeps_only_what_always_runs():
    selection = select([], _map(), current=[A, D, E], allow_empty=True)
    assert set(selection.tests) == {D, E}


def test_runner_and_plain_expressions():
    selection = select(["mail"], _map())
    assert selection.runner() == f"{D} {C} {E}"
    assert is_plain(selection.runner())
    assert not is_plain("suite/test_x.py::test_y[1]") and not is_plain("x; rm -rf /")


def test_markdown_annotates_tests_the_gate_excludes():
    text = render_markdown(select(["orders"], _map(), current=[A, B]), _map())
    assert f"| `{B}` | orders | inferred | orders (inferred) | excluded by `slow` |" in text
    assert "2 of 5 mapped test(s) selected; 1 carry no marker" in text
    assert "**Full suite.**" in render_markdown(select(["edge"], _map()), _map())


# ------------------------------------------------------------------ inputs


def test_service_lists_and_bad_names():
    assert parse_services("orders, mail orders") == ["mail", "orders"]
    assert parse_services(["orders,mail"]) == ["mail", "orders"]
    with pytest.raises(SelectionError, match="bad service name"):
        parse_services("orders;mail$")


def test_delta_counts_changed_one_sided_and_not_compared():
    delta = {
        "services": [
            {"service": "orders", "status": "changed"},
            {"service": "mail", "status": "one-sided"},
            {"service": "edge", "status": "not-compared"},
            {"service": "stock", "status": "unchanged"},
        ]
    }
    assert services_from_delta(delta) == ["edge", "mail", "orders"]
    with pytest.raises(SelectionError, match="unknown status"):
        services_from_delta({"services": [{"service": "x", "status": "maybe"}]})
    with pytest.raises(SelectionError, match="'services' list"):
        services_from_delta({"rows": []})


def _write_map(tmp_path: Path, smap: ServiceMap) -> Path:
    path = tmp_path / "map.json"
    path.write_text(smap.dumps())
    return path


def test_cli_reads_a_delta_from_stdin(tmp_path, monkeypatch, capsys):
    path = _write_map(tmp_path, _map())
    delta = {"services": [{"service": "mail", "status": "changed"}]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(delta)))
    args = ["--map", str(path), "select", "--delta", "-", "--no-collect", "--format", "runner"]
    assert svc.main(args, env={}) == 0
    assert capsys.readouterr().out.strip() == f"{D} {C} {E}"


def test_cli_json_and_bad_input(tmp_path, capsys):
    path = _write_map(tmp_path, _map())
    base = ["--map", str(path), "select", "--no-collect"]
    assert svc.main([*base, "--services", "orders", "--format", "json"], env={}) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"] is False and data["tests"][B]["excluded_by"] == ["slow"]
    assert svc.main([*base, "--services", "bad;name"], env={}) == 2
    assert svc.main([*base, "--delta", str(tmp_path / "missing.json")], env={}) == 2


def test_cli_nothing_selected_is_its_own_exit_code(tmp_path, capsys):
    path = _write_map(tmp_path, _map(tests={A: {"services": ["orders"], "source": "tag"}}, core=[]))
    args = ["--map", str(path), "select", "--allow-empty", "--no-collect", "--format", "runner"]
    assert svc.main(args, env={}) == 3  # an empty runner expression would mean "everything"
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ replay


def test_replay_verdicts_and_sizes(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_orders.py").write_text(
        "def test_order_total():\n    # DEF-1 regression\n    pass\n\n"
        "def test_order_refund():\n    '''DEF-2'''\n"
    )
    (suite / "test_mail.py").write_text("ISSUE = 'DEF-3'\n\ndef test_mail_sent():\n    pass\n")
    defects = [
        {"id": "DEF-1", "service": "orders"},  # named by A, selected for orders
        {"id": "DEF-2", "service": "orders"},  # named only by B, which the gate excludes
        {"id": "DEF-3", "service": "orders"},  # named by C (module-level), not selected
        {"id": "DEF-4", "service": "mail"},  # named by nothing
        {"id": "DEF-10", "service": "edge"},  # DEF-1 must not match DEF-10
    ]
    report = replay(defects, _map(), tmp_path)
    assert f"| DEF-1 | orders | SELECTED | `{A}` |" in report
    assert f"| DEF-2 | orders | SELECTED-NOT-IN-GATE | `{B}` |" in report
    assert f"| DEF-3 | orders | MISSED | `{C}` |" in report
    assert "| DEF-4 | mail | NO-ENV-TEST | - |" in report
    assert "| DEF-10 | edge | NO-ENV-TEST | - |" in report
    assert "| orders | 4 | 3 | 4 | 75% |" in report  # A, B, D, E selected; B out of gate
    assert "| edge | full suite | 4 | 4 | 100% |" in report
    assert "## Limits" in report and "a mention, not proof" in report


def test_replay_cli_writes_the_report_and_rejects_bad_rows(tmp_path):
    path = _write_map(tmp_path, _map())
    defects = tmp_path / "defects.json"
    defects.write_text(json.dumps({"defects": [{"id": "DEF-9", "service": "mail"}]}))
    out = tmp_path / "REPLAY.md"
    args = ["--map", str(path), "replay", "--defects", str(defects), "--out", str(out)]
    assert svc.main(args, env={}) == 0
    assert out.read_text().startswith("# Selection replay")
    defects.write_text(json.dumps({"defects": [{"id": "DEF-9"}]}))
    assert svc.main(args, env={}) == 2


# ------------------------------------------------------------------ CI switch


def _decide(env, **kwargs):
    kwargs.setdefault("smap_loader", _map)
    kwargs.setdefault("collector", lambda roots: [A, B, C, D, E])
    kwargs.setdefault("lane", lambda ids: list(ids))
    return ci_decide(env, **kwargs)


def test_switch_off_is_today_s_full_run():
    assert _decide({}) == svc.CiDecision(False)


def test_a_service_list_with_the_switch_off_fails_before_any_test():
    with pytest.raises(SelectionError, match="SELECTIVE_TESTS is off"):
        _decide({"CHANGED_SERVICES": "orders"})
    assert svc.main(["ci"], env={"CHANGED_SERVICES": "orders"}) == 2


ON = {"SELECTIVE_TESTS": "true"}


def _boom():
    raise SelectionError("map unreadable")


@pytest.mark.parametrize(
    ("env", "kwargs", "reason"),
    [
        ({}, {}, "no input"),
        ({"CHANGED_SERVICES": "orders;x"}, {}, "bad input"),
        ({"CHANGED_SERVICES": "orders", "OTHER_FILTER": "true"}, {}, "another filter"),
        ({"CHANGED_SERVICES": "orders", "SHARD_COUNT": "3"}, {}, "sharding is on (3 shards)"),
        ({"CHANGED_SERVICES": "orders", "SHARD_COUNT": "x"}, {}, "bad input"),
        ({"CHANGED_SERVICES": "orders"}, {"smap_loader": _boom}, "selector error"),
        ({"CHANGED_SERVICES": "unknown-svc"}, {}, "not in the map"),
        (
            {"CHANGED_SERVICES": "orders"},
            {"collector": lambda roots: [A, "suite/t.py::test_x[1]"]},
            "not plain",
        ),
        ({"CHANGED_SERVICES": "orders"}, {"lane": lambda ids: []}, "nothing selected is in"),
    ],
)
def test_every_case_it_cannot_apply_falls_back_with_one_reason(env, kwargs, reason):
    decision = _decide({**ON, **env}, **kwargs)
    assert not decision.selective and len(decision.reasons) == 1
    assert reason in decision.reasons[0]


def test_an_applicable_selection_is_handed_to_the_run_step(tmp_path, capsys):
    decision = _decide({**ON, "CHANGED_SERVICES": "mail", "SHARD_COUNT": "1"})
    assert decision == svc.CiDecision(True, f"{D} {C} {E}")


def test_ci_cli_writes_the_outputs(tmp_path, capsys):
    output = tmp_path / "out"
    assert svc.main(["ci"], env={**ON, "GITHUB_OUTPUT": str(output)}) == 0
    assert output.read_text() == "selective=false\nexpression=\n"
    assert "full suite: no input" in capsys.readouterr().out


def test_run_refuses_an_empty_or_odd_selection():
    assert svc.main(["run"], env={}) == 2
    assert svc.main(["run"], env={"SERVICE_SELECTION": "x; rm"}) == 2


def test_the_run_step_is_the_literal_gate_command_narrowed():
    from gate.run_inputs import default_command

    assert svc.gate_command([A]) == [default_command()[0], A, *default_command()[1:]]


def test_changed_services_input_is_validated_but_does_not_change_the_command():
    envs = ["qa"]
    assert plan({"changed_services": "orders, mail"}, environments=envs).is_default()
    with pytest.raises(RunInputsError, match="changed_services"):
        plan({"changed_services": "orders;mail"}, environments=envs)


def test_workflow_wiring():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["browser"]["steps"]
    names = [s.get("name") for s in steps]
    decide = steps[names.index("Service selection")]
    assert names.index("Service selection") < names.index("Run Smoke Tests")
    assert decide["run"] == "python -m gate.service_selection ci"
    assert set(decide["env"]) == {
        "SELECTIVE_TESTS",
        "CHANGED_SERVICES",
        "OTHER_FILTER",
        "SHARD_COUNT",
    }
    literal = steps[names.index("Run Smoke Tests")]
    narrowed = steps[names.index("Run the gate on the service selection")]
    assert literal["if"].endswith("steps.services.outputs.selective != 'true'")
    assert narrowed["if"].endswith("steps.services.outputs.selective == 'true'")
    assert narrowed["run"] == "python -m gate.service_selection run"
