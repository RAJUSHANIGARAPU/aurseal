"""pytest plugin used by ``results.catalogue.build_catalogue``: collect, never run.

Loaded with ``-p results._catalogue_collector`` in a ``--collect-only`` subprocess; writes
the collected cases and any collection errors to ``$RESULTS_CATALOGUE_OUT`` as JSON.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from results.catalogue import COLLECTOR_OUT_ENV, case_from_item, register_markers
from results.revision import revision_from_item, run_git_sha

_state: dict[str, list] = {"cases": [], "errors": [], "revisions": []}


def pytest_configure(config: pytest.Config) -> None:
    register_markers(config)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _state["errors"].append(f"{report.nodeid or '(session)'}: collection error")


def pytest_collection_finish(session: pytest.Session) -> None:
    git_sha = run_git_sha(Path(str(session.config.rootpath)))
    cases: dict[str, dict] = {}
    revisions: dict[str, dict] = {}
    for item in session.items:
        case = case_from_item(item)
        if case.case_id in cases:
            continue
        rev = revision_from_item(item, case, git_sha=git_sha)
        if rev is not None:
            case = replace(case, current_revision_id=rev.revision_id)
            revisions[case.case_id] = rev.to_json()
        cases[case.case_id] = case.to_json()
    _state["cases"] = list(cases.values())
    _state["revisions"] = list(revisions.values())


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    target = os.environ.get(COLLECTOR_OUT_ENV)
    if not target:
        return
    Path(target).write_text(json.dumps(_state), encoding="utf-8")
