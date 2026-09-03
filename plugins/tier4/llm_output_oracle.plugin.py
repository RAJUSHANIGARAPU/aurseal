"""
LLM output oracle plugin — assesses whether LLM output is stable.

``assessment`` was initialised to ``"stable"`` and the block that could change
it ran only when ``reports/agent_traces`` existed and held a ``.jsonl``. On a
checkout that has never produced a trace — every CI container, every fresh
clone — the plugin reported the output stable having read nothing at all. A
healthy verdict from an empty result set.

The status was worse: ``PluginResult(status="pass", ...)`` was a literal. Even
where the assessment did run and came back ``"variable"``, or came back
unrecognised and was correctly recorded as ``"unknown"``, the orchestrator saw
``pass``. The finding said one thing and the score said another, and only the
score gates the deploy.

Both are now derived from the assessment, and the file walk goes through
``_source_scan`` — this plugin runs at BACKGROUND priority, on a daemon thread
nobody joins, and it used to read and then separately re-read 3353
site-packages files.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus
from plugins.cost_governor import CostGovernor
from plugins.tier4._source_scan import relative_key, scan_source_files

logger = logging.getLogger(__name__)

_LLM_MARKERS = ("anthropic", "ClaudeLLMClient", "claude_client", "BaseLLMClient")

_DEFAULT_TRACES_DIR = Path("reports/agent_traces")


class LLMOutputOraclePlugin(BasePlugin):
    name = "llm-output-oracle"
    priority = PluginPriority.BACKGROUND
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        traces_dir = Path(context.get("traces_dir") or _DEFAULT_TRACES_DIR)
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = context.get("dry_run", False)

        scan = scan_source_files(source_dir, skip_tests=False)

        # Read once and keep the text. The old loop read every file to grep for
        # a marker and then read each matching file a second time to parse it.
        llm_sources: list[tuple[str, str]] = []
        for py_file in scan.files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            if any(marker in content for marker in _LLM_MARKERS):
                llm_sources.append((relative_key(py_file, source_dir), content))

        llm_files = [name for name, _ in llm_sources]

        if is_dry:
            # A preview, not a verdict. SKIP leaves the health score alone.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[{"llm_files": llm_files, "count": len(llm_files)}],
                dry_run=True,
            )

        if scan.truncated:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": f"source scan stopped at {scan.count} files; the tree "
                        "under source_dir was not read in full",
                        "source_dir": str(source_dir),
                    }
                ],
            )

        if not scan.files:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"reason": f"no python files under {source_dir}"}],
            )

        if not llm_sources:
            # Genuinely inapplicable: there is code here and none of it calls a
            # model, so there is no output for an oracle to be an oracle about.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[{"reason": "no LLM-calling code found", "files_scanned": scan.count}],
            )

        schema_checks = _return_types(llm_sources)
        assessment, reason = self._assess(traces_dir, governor)

        status = {
            "stable": PluginStatus.PASS.value,
            "variable": PluginStatus.WARN.value,
        }.get(assessment, PluginStatus.UNKNOWN.value)

        return PluginResult(
            status=status,
            findings=[
                {
                    "llm_files": llm_files,
                    "stability_assessment": assessment,
                    "assessment_reason": reason,
                    "schema_checks": schema_checks,
                }
            ],
        )

    def _assess(self, traces_dir: Path, governor: CostGovernor) -> tuple[str, str]:
        """Read the newest trace and ask the model how variable the output is.

        Every path that does not produce one of the two words asked for returns
        ``"unknown"`` with the reason attached, because "we have no traces" and
        "the output is stable" are the two answers this plugin most needs to
        keep apart.
        """
        if not traces_dir.is_dir():
            return "unknown", f"no traces directory at {traces_dir}"
        log_files = sorted(traces_dir.glob("*.jsonl"))
        if not log_files:
            return "unknown", f"no .jsonl traces under {traces_dir}"
        try:
            from api.llm.claude_client import ClaudeLLMClient

            llm = ClaudeLLMClient(model=governor.get_model("claude-haiku-4-5-20251001"))
            sample = log_files[-1].read_text(encoding="utf-8")[:1000]
            prompt = (
                "Review these LLM agent traces and assess output variability. "
                f"Reply with one word: stable or variable.\n\n{sample}"
            )
            reply = governor.cached_complete(prompt, llm.complete).strip().lower()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] variability assessment unavailable: %s", self.name, exc)
            return "unknown", str(exc)
        if reply in ("stable", "variable"):
            return reply, f"assessed from {log_files[-1].name}"
        # complete() returns "" when the model was not reached, and defaulting
        # that to "stable" reports a clean bill of health nobody checked.
        return "unknown", f"unusable reply: {reply[:80]!r}"


def _return_types(sources: list[tuple[str, str]]) -> list[dict]:
    """Annotated return types per file, parsed from text already in hand."""
    findings: list[dict] = []
    for name, content in sources:
        try:
            tree = ast.parse(content)
        except Exception:  # noqa: BLE001
            continue
        return_types = [
            ast.unparse(node.returns)
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns
        ]
        if return_types:
            findings.append({"file": name, "return_types": return_types[:5]})
    return findings


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = LLMOutputOraclePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "warn", "skip") else 1)
