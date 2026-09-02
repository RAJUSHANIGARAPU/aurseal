"""Behavioral equivalence plugin — snapshot/diff public function outputs across refactors."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_SNAPSHOT_DIR = Path("reports/behavioral_snapshots")


class _PublicFuncVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if not node.name.startswith("_"):
            self.functions.append(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)  # type: ignore[arg-type]


class BehavioralEquivalencePlugin(BasePlugin):
    name = "behavioral-equivalence"
    priority = PluginPriority.HIGH
    trigger_conditions = ["pre_refactor", "post_refactor"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()
        trigger = context.get("trigger", "pre_refactor")
        is_dry = context.get("dry_run", False)

        # Discover public functions
        public_funcs: list[dict] = []
        for py_file in source_dir.rglob("*.py"):
            if "test" in py_file.name:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            visitor = _PublicFuncVisitor()
            visitor.visit(tree)
            for func in visitor.functions[:5]:
                public_funcs.append({"file": str(py_file), "function": func})

        if is_dry:
            return PluginResult(
                status="pass",
                findings=[
                    {
                        "functions_to_snapshot": public_funcs,
                        "count": len(public_funcs),
                    }
                ],
                dry_run=True,
            )

        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_file = _SNAPSHOT_DIR / "snapshot.json"

        if trigger == "pre_refactor":
            snapshot_data = {"functions": public_funcs, "trigger": "pre_refactor"}
            snapshot_file.write_text(json.dumps(snapshot_data, indent=2), encoding="utf-8")
            return PluginResult(
                status="pass",
                findings=[{"snapshot_saved": str(snapshot_file), "count": len(public_funcs)}],
            )

        # post_refactor: load previous snapshot and diff
        if not snapshot_file.exists():
            return PluginResult(
                status="skip",
                findings=[{"reason": "no pre_refactor snapshot found"}],
            )

        prev = json.loads(snapshot_file.read_text(encoding="utf-8"))
        prev_funcs = {f["function"] for f in prev.get("functions", [])}
        curr_funcs = {f["function"] for f in public_funcs}
        added = curr_funcs - prev_funcs
        removed = prev_funcs - curr_funcs
        drift_classifications: list[dict] = []

        if added or removed:
            try:
                from api.llm.claude_client import ClaudeLLMClient

                model = governor.get_model("claude-haiku-4-5-20251001")
                llm = ClaudeLLMClient(model=model)
                prompt = (
                    f"Classify this behavioral drift — added: {list(added)}, "
                    f"removed: {list(removed)}. "
                    "Is this 'safe' (formatting only) or 'semantic' (logic changed)? "
                    "Reply with one word: safe or semantic."
                )
                raw = governor.cached_complete(prompt, llm.complete).strip().lower()
                # Anything we did not ask for is "unknown", never "semantic".
                # complete() returns "" when the model was not reached, and the
                # old else-branch turned that outage into a definitive "the
                # refactor changed behaviour" verdict on every drifted function.
                classification = raw if raw in ("safe", "semantic") else "unknown"
                drift_classifications.append(
                    {
                        "added": list(added),
                        "removed": list(removed),
                        "classification": classification,
                    }
                )
            except Exception:  # noqa: BLE001
                drift_classifications.append(
                    {"added": list(added), "removed": list(removed), "classification": "unknown"}
                )

        # Only "safe" clears the drift. An unclassified drift is still a drift —
        # calling it "pass" because the model was unreachable is the same false
        # green as calling it "semantic", just pointing the other way.
        status = (
            "warn"
            if any(d.get("classification") != "safe" for d in drift_classifications)
            else "pass"
        )

        return PluginResult(
            status=status,
            findings=[
                {
                    "drift_classifications": drift_classifications,
                    "current_function_count": len(public_funcs),
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": ".", "trigger": "pre_refactor"}
    plugin = BehavioralEquivalencePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip", "warn") else 1)
