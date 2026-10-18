"""Zero-code test factory: registered workflow YAML files become pytest cases.

A test module registers its files and re-exports the hook; every test that takes the
``yaml_case`` argument is parametrised over all cases of all registered files::

    from generators.yaml_suite import pytest_generate_tests  # noqa: F401 - the hook

    WORKFLOW_FILES = ["demo_sut/config/subscription.machine.yaml"]
    WORKFLOW_KNOWN_DEFECTS = {"subscription": {"suspended": "BUG-7"}}   # optional

    def test_generated(yaml_case, my_adapter):
        run_case(yaml_case, my_adapter)

Adding an entity is adding one line to ``WORKFLOW_FILES``. Without the module list the
files come from ``WORKFLOW_SUITE_FILES`` (paths separated by ``os.pathsep``); with
neither, the test gets no cases and pytest reports it as skipped with an empty
parameter set, never as a silent pass.

Optional module attributes, each keyed by machine key:
``WORKFLOW_KNOWN_MISSING`` ``{key: {(state, action): reason}}``,
``WORKFLOW_KNOWN_DEFECTS`` ``{key: {state: defect_id}}``, and
``WORKFLOW_FEASIBLE`` ``{key: callable(path) -> bool}``.
``WORKFLOW_ALL_PATHS = True`` keeps every path instead of a covering set.
``WORKFLOW_ADAPTER_MARKS`` (a list of marks) is added to the cases that need the system
under test's screens (render and path cases), for example a browser-lane marker, so the
definition-only cases still run where no browser is available.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from generators.config_suite import (
    ADAPTER_KINDS,
    generate_cases,
    path_cases,
    unreachable_cases,
    with_marks,
)
from generators.state_graph import TraversalPath
from generators.workflow_model import load

FIXTURE = "yaml_case"
FILES_ENV = "WORKFLOW_SUITE_FILES"


def registered_files(module: Any = None) -> list[Path]:
    """The module's ``WORKFLOW_FILES``, else the paths in ``$WORKFLOW_SUITE_FILES``."""
    listed = getattr(module, "WORKFLOW_FILES", None) if module is not None else None
    if listed is None:
        raw = os.environ.get(FILES_ENV, "")
        listed = [p for p in raw.split(os.pathsep) if p.strip()]
    return [Path(p) for p in listed]


def yaml_test_params(
    files: Iterable[str | Path],
    *,
    known_missing: Mapping[str, Mapping[tuple[str, str], str]] | None = None,
    known_defects: Mapping[str, Mapping[str, str]] | None = None,
    feasible: Mapping[str, Callable[[TraversalPath], bool]] | None = None,
    cover_only: bool = True,
    adapter_marks: Iterable[Any] = (),
) -> list[Any]:
    """Render, reachability and traversal-path cases for every file, in file order."""
    params: list[Any] = []
    for path in files:
        definition = load(path)
        key = definition.key
        params += generate_cases(definition, known_missing=(known_missing or {}).get(key))
        params += unreachable_cases(definition, (known_defects or {}).get(key))
        params += path_cases(definition, feasible=(feasible or {}).get(key), cover_only=cover_only)
    return with_marks(params, ADAPTER_KINDS, adapter_marks)


def pytest_generate_tests(metafunc: Any) -> None:
    """Parametrise ``yaml_case`` over the registered workflow files."""
    if FIXTURE not in metafunc.fixturenames:
        return
    module = metafunc.module
    params = yaml_test_params(
        registered_files(module),
        known_missing=getattr(module, "WORKFLOW_KNOWN_MISSING", None),
        known_defects=getattr(module, "WORKFLOW_KNOWN_DEFECTS", None),
        feasible=getattr(module, "WORKFLOW_FEASIBLE", None),
        cover_only=not getattr(module, "WORKFLOW_ALL_PATHS", False),
        adapter_marks=getattr(module, "WORKFLOW_ADAPTER_MARKS", ()),
    )
    metafunc.parametrize(FIXTURE, params)
