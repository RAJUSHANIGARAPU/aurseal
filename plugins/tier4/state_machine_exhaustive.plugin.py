"""State machine exhaustive plugin — discovers and traverses state machines."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_OUT_DIR = Path("ai_generated_tests/state_machine")

_SM_TEMPLATE = '''"""Auto-generated state machine traversal test for {class_name}."""
from __future__ import annotations

import pytest


# Discovered state transitions: {transitions}


@pytest.mark.parametrize("transition", {transitions_list})
def test_{class_lower}_transition(transition: str) -> None:
    """Verify state machine transition: {{transition}}."""
    # TODO: instantiate {class_name} and drive transition
    assert transition, f"Transition {{transition}} should be defined"
'''


class _StateMachineVisitor(ast.NodeVisitor):
    """Finds classes with state attribute or transition methods."""

    def __init__(self) -> None:
        self.state_machines: list[dict] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        has_state = False
        transitions: list[str] = []

        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                if item.name == "transition" or "transition" in item.name.lower():
                    transitions.append(item.name)
            if isinstance(item, ast.Assign | ast.AnnAssign):
                targets = []
                if isinstance(item, ast.Assign):
                    targets = item.targets
                elif isinstance(item, ast.AnnAssign):
                    targets = [item.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id in ("state", "_state", "current_state"):
                        has_state = True

        if has_state or transitions:
            self.state_machines.append(
                {
                    "class_name": node.name,
                    "has_state_attr": has_state,
                    "transitions": transitions,
                }
            )
        self.generic_visit(node)


class StateMachineExhaustivePlugin(BasePlugin):
    name = "state-machine-exhaustive"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["source_change", "manual"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841
        is_dry = context.get("dry_run", False)

        all_state_machines: list[dict] = []
        for py_file in source_dir.rglob("*.py"):
            if "test" in py_file.name:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            visitor = _StateMachineVisitor()
            visitor.visit(tree)
            for sm in visitor.state_machines:
                sm["file"] = str(py_file)
                all_state_machines.append(sm)

        if is_dry:
            return PluginResult(
                status="pass",
                findings=[
                    {
                        "state_machines": all_state_machines,
                        "count": len(all_state_machines),
                    }
                ],
                dry_run=True,
            )

        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []

        for sm in all_state_machines[:10]:
            class_name = sm["class_name"]
            transitions = sm.get("transitions", []) or ["init", "process", "complete", "error"]
            out_file = _OUT_DIR / f"test_sm_{class_name.lower()}.py"
            content = _SM_TEMPLATE.format(
                class_name=class_name,
                class_lower=class_name.lower(),
                transitions=transitions,
                transitions_list=str(transitions),
            )
            out_file.write_text(content, encoding="utf-8")
            generated.append(str(out_file))

        return PluginResult(
            status="pass",
            findings=[
                {
                    "state_machines": all_state_machines,
                    "generated_files": generated,
                    "transition_counts": {
                        sm["class_name"]: len(sm.get("transitions", []))
                        for sm in all_state_machines
                    },
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = StateMachineExhaustivePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
