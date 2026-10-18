"""The zero-code factory: registered workflow files become parametrised pytest cases."""

from __future__ import annotations

from pathlib import Path

from generators import yaml_suite
from generators.yaml_suite import registered_files, yaml_test_params

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[2]
MACHINE = ROOT / "demo_sut" / "config" / "subscription.machine.yaml"


def test_yaml_entity_yields_render_and_reachability_cases():
    params = yaml_test_params([MACHINE], known_defects={"subscription": {"suspended": "BUG-9"}})
    kinds = {p.values[0].kind for p in params}
    assert kinds == {
        "config_key",
        "initial_reachable",
        "declared_actions",
        "action_renders",
        "state_reachable",
        "path",
    }
    ids = [p.id for p in params]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert "subscription:state_reachable:suspended" in ids


def test_two_files_give_the_cases_of_both(tmp_path):
    other = tmp_path / "order.yaml"
    other.write_text("key: order\ninitial_states: [new]\nstates:\n  new: {}\n")
    ids = [p.id for p in yaml_test_params([MACHINE, other])]
    assert any(i.startswith("subscription:") for i in ids)
    assert "order:config_key" in ids and "order:state_reachable:new" in ids


def test_files_come_from_the_module_list_else_the_environment(monkeypatch, tmp_path):
    class Module:
        WORKFLOW_FILES = ["a.yaml"]

    assert registered_files(Module) == [Path("a.yaml")]
    monkeypatch.setenv(yaml_suite.FILES_ENV, f"x.yaml{__import__('os').pathsep}y.yaml")
    assert registered_files(object()) == [Path("x.yaml"), Path("y.yaml")]
    monkeypatch.delenv(yaml_suite.FILES_ENV)
    assert registered_files(None) == []


def test_the_hook_parametrises_a_module_end_to_end(pytester):
    pytester.makeini(f"[pytest]\npythonpath = {ROOT}\n")
    pytester.makepyfile(
        test_entities=f"""
        import pytest
        from generators.config_suite import run_case
        from generators.workflow_model import load
        from generators.yaml_suite import pytest_generate_tests  # noqa: F401
        from tests.generators._fake_machine import FakeMachine

        WORKFLOW_FILES = [{str(MACHINE)!r}]
        WORKFLOW_KNOWN_DEFECTS = {{"subscription": {{"suspended": "BUG-9"}}}}

        @pytest.fixture
        def sut():
            return FakeMachine(load(WORKFLOW_FILES[0]))

        def test_generated(yaml_case, sut):
            run_case(yaml_case, sut)
        """
    )
    result = pytester.runpytest_subprocess(
        "-p", "verification.pytest_plugin", "-p", "no:cacheprovider"
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("xfailed") == 1, result.stdout.str()
    assert outcomes.get("passed", 0) > 20 and "failed" not in outcomes


def test_adapter_marks_go_only_to_cases_that_need_the_screens():
    import pytest

    params = yaml_test_params([MACHINE], adapter_marks=[pytest.mark.slow])
    for p in params:
        names = [m.name for m in p.marks]
        assert ("slow" in names) == (p.values[0].kind in ("action_renders", "path")), p.id
