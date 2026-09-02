"""LLM output oracle plugin — checks schema stability of LLM-calling code."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_LLM_MARKERS = ("anthropic", "ClaudeLLMClient", "claude_client", "BaseLLMClient")


class LLMOutputOraclePlugin(BasePlugin):
    name = "llm-output-oracle"
    priority = PluginPriority.BACKGROUND
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = context.get("dry_run", False)

        llm_files: list[str] = []
        for py_file in source_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            if any(marker in content for marker in _LLM_MARKERS):
                llm_files.append(str(py_file))

        if is_dry:
            return PluginResult(
                status="pass",
                findings=[{"llm_files": llm_files, "count": len(llm_files)}],
                dry_run=True,
            )

        # Check for return type consistency via AST
        stability_findings: list[dict] = []
        for file_path in llm_files:
            py_file = Path(file_path)
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            return_types: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.returns:
                    return_types.append(ast.unparse(node.returns))
            if return_types:
                stability_findings.append({"file": file_path, "return_types": return_types[:5]})

        # Use Claude to assess variability from logs if any exist
        assessment = "stable"
        log_dir = Path("reports/agent_traces")
        if log_dir.exists():
            log_files = list(log_dir.glob("*.jsonl"))
            if log_files:
                try:
                    from api.llm.claude_client import ClaudeLLMClient

                    model = governor.get_model("claude-haiku-4-5-20251001")
                    llm = ClaudeLLMClient(model=model)
                    sample = log_files[-1].read_text(encoding="utf-8")[:1000]
                    prompt = (
                        f"Review these LLM agent traces and assess output variability. "
                        f"Reply with one word: stable or variable.\n\n{sample}"
                    )
                    assessment = governor.cached_complete(prompt, llm.complete).strip().lower()
                    # Same trap as behavioral_equivalence: complete() returns ""
                    # when the model was not reached, and defaulting that to
                    # "stable" reports a clean bill of health nobody checked.
                    if assessment not in ("stable", "variable"):
                        assessment = "unknown"
                except Exception:  # noqa: BLE001
                    assessment = "unknown"

        return PluginResult(
            status="pass",
            findings=[
                {
                    "llm_files": llm_files,
                    "stability_assessment": assessment,
                    "schema_checks": stability_findings,
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = LLMOutputOraclePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
