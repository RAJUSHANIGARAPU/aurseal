"""Integration graph plugin — maps service-to-service calls and generates integration tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor


class _CallVisitor(ast.NodeVisitor):
    """Collects requests.get/post/put/delete call URLs from AST."""

    def __init__(self) -> None:
        self.endpoints: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute) and node.func.attr in (
            "get",
            "post",
            "put",
            "delete",
            "patch",
        ):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "requests":
                if node.args and isinstance(node.args[0], ast.Constant):
                    self.endpoints.append(str(node.args[0].value))
        self.generic_visit(node)


class IntegrationGraphPlugin(BasePlugin):
    name = "integration-graph"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["dependency_change"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        dep_map: dict[str, list[str]] = {}

        for src_file in source_dir.rglob("*.py"):
            try:
                source = src_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except Exception:  # noqa: BLE001
                continue
            visitor = _CallVisitor()
            visitor.visit(tree)
            if visitor.endpoints:
                dep_map[str(src_file)] = visitor.endpoints

        if context.get("dry_run"):
            return PluginResult(
                status="pass",
                findings=[{"dependency_map": dep_map, "files_scanned": len(dep_map)}],
                dry_run=True,
            )

        governor = context.get("cost_governor") or CostGovernor()
        out_dir = Path("ai_generated_tests/integration")
        out_dir.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []

        unique_endpoints: set[str] = set()
        for endpoints in dep_map.values():
            unique_endpoints.update(endpoints)

        for endpoint in list(unique_endpoints)[:10]:
            slug = endpoint.replace("://", "_").replace("/", "_").replace(".", "_")[:40]
            out_file = out_dir / f"test_integration_{slug}.py"
            content = (
                f'"""Auto-generated integration test for {endpoint}."""\n\n'
                "import pytest\nimport requests\n\n\n"
                f"def test_endpoint_{slug}() -> None:\n"
                f'    """Verify {endpoint} returns 200."""\n'
                f'    resp = requests.get("{endpoint}", timeout=10)\n'
                "    assert resp.status_code == 200\n"
            )
            out_file.write_text(content, encoding="utf-8")
            generated.append(str(out_file))
            governor.record("none", 0, 0.0)

        return PluginResult(
            status="pass",
            findings=[
                {
                    "dependency_map": dep_map,
                    "generated_files": generated,
                    "unique_endpoints": len(unique_endpoints),
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = IntegrationGraphPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
