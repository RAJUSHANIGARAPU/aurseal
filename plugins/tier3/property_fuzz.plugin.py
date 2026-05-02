"""Property fuzz plugin — generates Hypothesis-based property tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor


class _FunctionVisitor(ast.NodeVisitor):
    """Collects top-level function definitions."""

    def __init__(self) -> None:
        self.functions: list[tuple[str, list[str]]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if not node.name.startswith("_"):
            args = [a.arg for a in node.args.args if a.arg != "self"]
            self.functions.append((node.name, args))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)  # type: ignore[arg-type]


_PROP_TEMPLATE = '''"""Auto-generated Hypothesis property tests for {module}."""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st


{tests}
'''

_TEST_TEMPLATE = """@given({strategies})
@settings(max_examples=50)
def test_{func_name}_properties({args}) -> None:
    \"\"\"Property test for {func_name} — should not raise.\"\"\"
    try:
        from {module_import} import {func_name}
        {func_name}({call_args})
    except (TypeError, ValueError, AttributeError):
        pass  # expected for boundary inputs
"""


class PropertyFuzzPlugin(BasePlugin):
    name = "property-fuzz"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["source_change", "manual"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = context.get("dry_run", False)
        out_dir = Path("ai_generated_tests/property")

        targeted_functions: list[dict] = []
        for py_file in source_dir.rglob("*.py"):
            if "test" in py_file.name or "plugin" in str(py_file):
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except Exception:  # noqa: BLE001
                continue
            visitor = _FunctionVisitor()
            visitor.visit(tree)
            for func_name, args in visitor.functions[:3]:
                targeted_functions.append(
                    {"file": str(py_file), "function": func_name, "args": args}
                )

        if is_dry:
            return PluginResult(
                status="pass",
                findings=[
                    {
                        "targeted_functions": targeted_functions,
                        "count": len(targeted_functions),
                    }
                ],
                dry_run=True,
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []
        total_tokens = 0

        for entry in targeted_functions[:10]:
            func_name = entry["function"]
            args = entry["args"]
            src_file = Path(entry["file"])
            module_import = str(src_file.with_suffix("")).replace("/", ".").replace("\\", ".")

            strategies = ", ".join(
                f"{a}=st.one_of(st.text(), st.integers(), st.none())" for a in args
            )
            call_args = ", ".join(args)

            # Optionally use Claude to seed better boundary values
            try:
                from api.llm.claude_client import ClaudeLLMClient

                model = governor.get_model("claude-haiku-4-5-20251001")
                llm = ClaudeLLMClient(model=model)
                prompt = (
                    f"For function `{func_name}({call_args})`, suggest Hypothesis strategies "
                    "for property-based testing. Return only the strategies= part, e.g. "
                    "x=st.integers(min_value=0), y=st.text(max_size=100)"
                )
                hint = governor.cached_complete(prompt, llm.complete)
                if hint and not hint.startswith("Claude API error") and "st." in hint:
                    strategies = hint.strip().rstrip(",")
                total_tokens += len(hint) // 4 if hint else 0
            except Exception:  # noqa: BLE001
                pass

            test_code = _TEST_TEMPLATE.format(
                func_name=func_name,
                strategies=strategies,
                args=call_args,
                call_args=call_args,
                module_import=module_import,
            )
            module_file = out_dir / f"test_prop_{src_file.stem}.py"
            module_content = _PROP_TEMPLATE.format(module=src_file.stem, tests=test_code)
            module_file.write_text(module_content, encoding="utf-8")
            generated.append(str(module_file))

        return PluginResult(
            status="pass",
            findings=[
                {
                    "generated_files": generated,
                    "targeted_functions": targeted_functions,
                }
            ],
            tokens_used=total_tokens,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = PropertyFuzzPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
