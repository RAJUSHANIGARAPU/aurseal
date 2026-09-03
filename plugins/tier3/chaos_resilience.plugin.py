"""Chaos resilience plugin — asks a model for fault scenarios from the import graph.

The whole output of this plugin is model text, so an outage had nowhere to hide
except in the artefact. It hid there: when the call failed the plugin wrote five
copies of "Network partition between services" into ``chaos_scenarios.md`` and
returned ``pass``. On disk that is indistinguishable from a real answer — same
filename, same shape, a plausible-sounding scenario — and the next person to
read it has no way to know the model was never reached.

The guard that should have caught it (``response.startswith("Claude API error")``)
could not, because ``complete()`` is contracted to return ``""`` on failure, not
prose. ``complete_result()`` exists for exactly this caller: it returns text or a
named ``LLMFailure``, so "the model produced nothing useful" and "the model was
never reached" are different values again.

So there are now only two outcomes. Either real scenarios were parsed out of a
real reply and the file is written, or nothing is written and the status is
``unknown`` with the reason named. The canned fallback is gone; a placeholder
that survives the run is worse than no file, because it is quotable.

This plugin cannot return ``fail``. It writes scenarios for a human to run; it
never executes a fault or observes the system under one, so it has no way to
learn that something is wrong. ``fail`` here would be a lie in the other
direction.
"""

from __future__ import annotations

import ast
import logging
import re
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus
from plugins.cost_governor import CostGovernor
from plugins.tier3._source_scan import iter_source_files

logger = logging.getLogger(__name__)

_OUT_FILE = Path("ai_generated_tests/chaos/chaos_scenarios.md")

#: A scenario heading as the prompt asks for it. Anchored at line start so a
#: mention of "## Scenario" inside a description does not split the reply.
_HEADING = re.compile(r"^##\s+(?:Scenario\b.*)$", re.MULTILINE)


def _scan_imports(source_dir: Path) -> dict[str, list[str]]:
    """Return module -> imported modules map for the project's own files."""
    graph: dict[str, list[str]] = {}
    for py_file in iter_source_files(source_dir).files:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        if imports:
            graph[str(py_file)] = imports
    return graph


def _parse_scenarios(reply: str) -> list[str]:
    """Split a reply into scenario blocks, keeping each heading with its body.

    Returns an empty list when the reply carries no scenario heading at all —
    the model answered something, but not the thing that was asked for, and a
    caller must not write that out as though it were scenarios.
    """
    matches = list(_HEADING.finditer(reply))
    if not matches:
        return []
    blocks: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(reply)
        block = reply[match.start() : end].strip()
        if block:
            blocks.append(block)
    return blocks


class ChaosResiliencePlugin(BasePlugin):
    name = "chaos-resilience"
    priority = PluginPriority.BACKGROUND
    trigger_conditions = ["manual"]

    def _ask(self, governor: CostGovernor, prompt: str) -> tuple[str, str]:
        """Return (text, failure_reason). Exactly one of the two is non-empty.

        ``cached_complete`` is kept — dropping it would turn a repeated prompt
        into a repeated bill — but the call it wraps returns a ``Completion``,
        and the failure is captured here rather than flattened into ``""``. An
        empty string still goes back to the governor, which is what stops an
        outage being memoised against the prompt for the rest of the process.
        """
        failure: list[str] = []

        def _call(text: str) -> str:
            from api.llm.claude_client import ClaudeLLMClient

            llm = ClaudeLLMClient(model=governor.get_model("claude-sonnet-4-6"))
            completion = llm.complete_result(text)
            if not completion.ok:
                failure.append(str(completion.failure))
                return ""
            return completion.text

        try:
            reply = governor.cached_complete(prompt, _call)
        except Exception as exc:  # noqa: BLE001 — any client fault is still "no answer"
            logger.warning("chaos-resilience: model call raised %s: %s", type(exc).__name__, exc)
            return "", f"{type(exc).__name__}: {exc}"

        if reply and reply.strip():
            return reply, ""
        return "", failure[0] if failure else "the model returned no text"

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = bool(context.get("dry_run", False))
        scenario_count = 2 if is_dry else 5

        import_graph = _scan_imports(source_dir)
        if not import_graph:
            # "Project has 0 modules with cross-module dependencies" is not an
            # architecture summary, and scenarios derived from it would describe
            # nothing. Usually it means source_dir is wrong.
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": "no importing modules found — nothing to derive scenarios from",
                        "source_dir": str(source_dir),
                    }
                ],
                dry_run=is_dry,
            )

        arch_summary = f"Project has {len(import_graph)} modules with cross-module dependencies."
        prompt = (
            f"You are a chaos engineering expert. Given this architecture summary: "
            f"{arch_summary}\n\nGenerate {scenario_count} chaos/fault injection scenarios. "
            "Format each as: ## Scenario N: <title>\n<description>\n**Fault**: <fault>\n"
            "**Expected behavior**: <behavior>\n"
        )
        reply, failure_reason = self._ask(governor, prompt)
        if not reply:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": f"no scenarios were generated: {failure_reason}",
                        "module_count": len(import_graph),
                    }
                ],
                dry_run=is_dry,
            )

        scenarios = _parse_scenarios(reply)
        if not scenarios:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": "the reply carried no '## Scenario' heading — nothing parseable",
                        "reply_preview": reply.strip()[:200],
                    }
                ],
                dry_run=is_dry,
            )

        written: str | None = None
        if not is_dry:
            _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _OUT_FILE.write_text(
                "# Chaos Scenarios\n\n" + "\n\n".join(scenarios) + "\n", encoding="utf-8"
            )
            written = str(_OUT_FILE)

        return PluginResult(
            status=PluginStatus.PASS.value,
            findings=[
                {
                    "scenarios": scenarios[:scenario_count],
                    "scenario_count": len(scenarios),
                    "requested_count": scenario_count,
                    "module_count": len(import_graph),
                    "output_file": written,
                }
            ],
            dry_run=is_dry,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-dir", default=".")
    args = parser.parse_args()
    ctx: dict = {"source_dir": args.source_dir}
    plugin = ChaosResiliencePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in (PluginStatus.PASS.value, PluginStatus.WARN.value) else 1)
