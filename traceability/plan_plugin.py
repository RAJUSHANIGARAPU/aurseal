"""``pytest --plan plans/x.yaml``: run exactly the tests a plan selects.

Everything outside the plan is deselected (reported as deselected, like ``-m``), so the
normal runner, xdist, the run-trust gate and the recorder all apply unchanged. A plan
that matches nothing leaves no test selected and pytest exits 5, which CI must not mask.

Load with ``-p traceability.plan_plugin`` (or list it in the root ``pytest_plugins``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from traceability.plans import PlanError, load_plan, select
from traceability.pytest_plugin import marker_ids
from traceability.requirements import Registry

PLUGIN_NAME = "traceability-plan"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("traceability").addoption(
        "--plan",
        dest="trace_plan",
        default=None,
        metavar="PATH",
        help="run only the tests the plan file selects",
    )


class PlanPlugin:
    def __init__(self, path: Path, rootdir: Path) -> None:
        try:
            self.plan = load_plan(path)
        except (PlanError, ValueError) as exc:
            raise pytest.UsageError(f"--plan: {exc}") from exc
        self.registry = Registry.load(rootdir=rootdir)

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]):
        described = [
            {
                "nodeid": item.nodeid,
                "markers": sorted({m.name for m in item.iter_markers()}),
                "requirements": marker_ids(item),
            }
            for item in items
        ]
        try:
            selection = select(self.plan, described, self.registry)
        except PlanError as exc:
            raise pytest.UsageError(f"--plan {self.plan.path}: {exc}") from exc
        keep = set(selection.nodeids)
        dropped = [i for i in items if i.nodeid not in keep]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = [i for i in items if i.nodeid in keep]
        for unmatched in selection.unmatched_selectors:
            config.issue_config_time_warning(
                pytest.PytestWarning(f"plan {self.plan.id}: {unmatched} matched nothing"),
                stacklevel=2,
            )

    def pytest_report_header(self, config: pytest.Config) -> str:
        return f"plan: {self.plan.id} (revision {self.plan.revision[:12]})"


def pytest_configure(config: pytest.Config) -> None:
    path = config.getoption("trace_plan", None)
    if path and not config.pluginmanager.hasplugin(PLUGIN_NAME):
        rootdir = Path(str(config.rootpath))
        target = Path(path) if Path(path).is_absolute() else Path.cwd() / path
        config.pluginmanager.register(PlanPlugin(target, rootdir), PLUGIN_NAME)
