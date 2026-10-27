"""Selective runs from changed paths (gate.selective): every rule leans toward running more."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from gate.selective import (
    MAP_FILE,
    OFFLINE_MARKER,
    Area,
    AreaMap,
    MapError,
    Selection,
    check_map,
    dependency_index,
    main,
    select_for_paths,
)

ROOT = Path(__file__).resolve().parents[2]

AREAS = AreaMap(
    always_full=("conftest.py", "**/conftest.py", "core/**", "pytest.ini"),
    areas=(
        Area("billing", paths=("billing/**/*.py",), tests=("tests/billing/",)),
        Area("search", paths=("search/**/*.py",), tests=("tests/search/",), markers=("smoke",)),
    ),
)


def _select(*paths: str, root: Path = ROOT, **kwargs) -> Selection:
    return select_for_paths(paths, AREAS, root, shared=frozenset(), **kwargs)


# ------------------------------------------------------------------ the rules


def test_an_area_path_selects_the_area_tests_and_says_why():
    selection = _select("billing/invoice.py")
    assert not selection.full
    assert dict(selection.tests) == {"tests/billing/": ["area billing (billing/invoice.py)"]}
    assert "tests/billing/: area billing (billing/invoice.py)" in selection.summary()


def test_an_area_marker_is_selected_alongside_its_paths():
    selection = _select("search/index.py")
    assert set(selection.tests) == {"tests/search/"} and set(selection.markers) == {"smoke"}


@pytest.mark.parametrize("path", ["conftest.py", "tests/billing/conftest.py", "core/x.py"])
def test_shared_infrastructure_selects_the_full_suite(path):
    selection = _select(path, "billing/invoice.py")
    assert selection.full and "shared infrastructure" in selection.full_reasons[0]


def test_a_path_no_area_claims_selects_the_full_suite():
    selection = _select("billing/invoice.py", "somewhere/else.txt")
    assert selection.full
    assert selection.full_reasons == ["somewhere/else.txt belongs to no area"]


def test_a_non_python_file_inside_an_area_is_unknown():
    """Area globs name .py files: who reads a data file is not declared."""
    assert _select("billing/rates.json").full


def test_a_file_every_test_loads_selects_the_full_suite():
    selection = select_for_paths(
        ["billing/invoice.py"], AREAS, ROOT, shared=frozenset({"billing/invoice.py"})
    )
    assert selection.full and "loaded by every test" in selection.full_reasons[0]


def test_a_changed_test_module_selects_itself():
    selection = _select("tests/gate/test_selective.py")
    assert dict(selection.tests) == {
        "tests/gate/test_selective.py": ["the test module itself changed"]
    }


def test_a_deleted_test_module_selects_nothing_and_says_so():
    selection = _select("tests/gate/test_gone.py")
    assert selection.empty and "was deleted" in selection.notes[0]


def test_a_helper_or_data_file_under_a_test_root_selects_the_full_suite():
    assert _select("tests/gate/helpers.py").full
    assert _select("tests/gate/fixtures/data.json").full


def test_an_empty_change_set_runs_everything_unless_allowed():
    assert _select().full
    allowed = _select(allow_empty=True)
    assert allowed.empty and not allowed.full and allowed.notes


# ------------------------------------------------------------------ pytest arguments


def test_full_selection_keeps_the_lane_command():
    selection = _select("core/x.py")
    assert selection.pytest_args(("tests/",), "not slow") == ["tests/", "-m", "not slow"]


def test_paths_are_kept_inside_the_lane_scope():
    selection = Selection()
    selection.tests["tests/a/"].append("x")
    selection.tests["ui/tests/"].append("x")
    assert selection.pytest_args(("tests/",), "not slow") == ["tests/a/", "-m", "not slow"]
    assert selection.pytest_args(("api/tests/",)) is None


def test_markers_alone_are_and_ed_with_the_lane_marker():
    selection = Selection()
    selection.markers["smoke"].append("x")
    assert selection.pytest_args(("tests/",), "not slow") == [
        "tests/",
        "-m",
        "(smoke) and (not slow)",
    ]


def test_paths_and_markers_together_need_node_ids():
    """pytest cannot express "these paths OR this marker" in one command line."""
    selection = _select("search/index.py")
    assert selection.pytest_args(("tests/",)) is None


# ------------------------------------------------------------------ the map file


def test_load_rejects_a_malformed_map(tmp_path):
    bad = tmp_path / "map.json"
    bad.write_text(json.dumps({"areas": {"a": {"paths": ["x/**"]}}}))
    with pytest.raises(MapError, match="non-empty 'tests'"):
        AreaMap.load(bad)
    bad.write_text("{not json")
    with pytest.raises(MapError, match="cannot read"):
        AreaMap.load(bad)


def _repo(tmp_path: Path) -> Path:
    files = {
        "pytest.ini": "[pytest]\ntestpaths =\n    tests\nmarkers =\n    smoke\n",
        "conftest.py": "import shared.base\n",
        "shared/__init__.py": "",
        "shared/base.py": "",
        "billing/__init__.py": "",
        "billing/invoice.py": "from . import rates\n",
        "billing/rates.py": "",
        "search/__init__.py": "",
        "search/index.py": "",
        "tests/__init__.py": "",
        "tests/billing/__init__.py": "",
        "tests/billing/test_invoice.py": "from billing.invoice import x\n",
        "tests/reports/__init__.py": "",
        "tests/reports/conftest.py": "import importlib\nPLUGIN = 'search.index'\n",
        "tests/reports/test_report.py": "def test_x():\n    pass\n",
        "tests/reports/test_rates.py": "def test_x():\n    from billing import rates\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return tmp_path


def test_dependency_index_follows_relative_lazy_string_and_conftest_imports(tmp_path):
    index = dependency_index(_repo(tmp_path))
    assert "billing/rates.py" in index["tests/billing/test_invoice.py"]  # via a relative import
    assert "billing/rates.py" in index["tests/reports/test_rates.py"]  # lazy, inside a function
    assert "search/index.py" in index["tests/reports/test_report.py"]  # named in its conftest
    assert "shared/base.py" in index["tests/reports/test_report.py"]  # the root conftest


def test_check_map_names_a_test_the_area_would_miss(tmp_path):
    root = _repo(tmp_path)
    area_map = AreaMap(
        always_full=("conftest.py",),
        areas=(Area("billing", paths=("billing/**/*.py",), tests=("tests/billing/",)),),
    )
    problems = check_map(area_map, root)
    assert problems == [
        "area billing: tests/reports/test_rates.py loads billing/__init__.py (+1 more) "
        "but is not in the area's tests"
    ]
    fixed = AreaMap(
        always_full=("conftest.py",),
        areas=(
            Area(
                "billing",
                paths=("billing/**/*.py",),
                tests=("tests/billing/", "tests/reports/test_rates.py"),
            ),
        ),
    )
    assert check_map(fixed, root) == []


def test_check_map_ignores_files_every_test_loads(tmp_path):
    root = _repo(tmp_path)
    area_map = AreaMap(
        always_full=("conftest.py",),
        areas=(Area("shared", paths=("shared/**/*.py",), tests=("tests/billing/",)),),
    )
    assert check_map(area_map, root) == []


def test_check_map_names_dead_references(tmp_path):
    root = _repo(tmp_path)
    area_map = AreaMap(
        always_full=("nowhere/**",),
        areas=(
            Area(
                "billing",
                paths=("billing/**/*.py", "gone/**"),
                tests=("tests/billing/", "tests/reports/", "tests/missing/"),
                markers=("smoke", "nightly"),
            ),
        ),
    )
    problems = check_map(area_map, root)
    assert "always_full glob 'nowhere/**' matches no file" in problems
    assert "area billing: glob 'gone/**' matches no file" in problems
    assert "area billing: test path tests/missing/ does not exist" in problems
    assert "area billing: marker 'nightly' is not declared" in problems


def test_the_committed_map_references_only_what_exists_and_misses_no_importer():
    """The map can over-select; this keeps it from quietly under-selecting."""
    assert check_map(AreaMap.load(MAP_FILE), ROOT) == []


# ------------------------------------------------------------------ CLI


def test_cli_select_json(capsys):
    assert main(["select", "--paths", "tools/push_guard.py", "--format", "json"], env={}) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"] is False and "tests/tools/" in data["tests"]


def test_cli_run_dry_run_on_a_full_selection_is_the_offline_lane(capsys):
    assert main(["run", "--dry-run", "--paths", "pytest.ini"], env={}) == 0
    out = capsys.readouterr().out
    assert "pytest.ini is shared infrastructure" in out
    assert f"tests/ -m '{OFFLINE_MARKER}' -n auto" in out


def test_cli_a_missing_diff_base_runs_everything(capsys):
    assert main(["select", "--base", "0000000"], env={}) == 0
    assert "Full suite" in capsys.readouterr().out


def test_cli_bad_map_exits_two(tmp_path, capsys):
    bad = tmp_path / "map.json"
    bad.write_text("[]")
    assert main(["check", "--map", str(bad)], env={}) == 2


# ------------------------------------------------------------------ workflow wiring


def _offline_steps() -> dict[str, dict]:
    import yaml

    data = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
    return {s.get("name"): s for s in data["jobs"]["offline"]["steps"]}


def test_run_narrows_exactly_the_literal_offline_step():
    """`run` rebuilds the offline lane from OFFLINE_SCOPE/OFFLINE_MARKER; they must not drift."""
    literal = _offline_steps()["Run Offline Tests"]
    assert literal["run"] == f'pytest tests/ -n auto -m "{OFFLINE_MARKER}"'


def test_the_switch_picks_exactly_one_of_the_two_offline_steps():
    steps = _offline_steps()
    literal, selective = (
        steps["Run Offline Tests"],
        steps["Run the offline tests this change can affect"],
    )
    assert literal["if"] == "vars.SELECTIVE_TESTS != 'true' || github.event_name != 'pull_request'"
    assert (
        selective["if"] == "vars.SELECTIVE_TESTS == 'true' && github.event_name == 'pull_request'"
    )
    assert selective["run"] == "python -m gate.selective run"
    assert literal["env"].items() <= selective["env"].items()  # same run-trust and settings
