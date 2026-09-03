"""
Unit AI plugin — generates unit tests from Python source using Claude.

An outage used to be indistinguishable from a clean run. ``api/llm/base.py``
documents ``complete()`` as returning ``""`` when the model was not reached, so
the ``except Exception`` around the call never fired for a 401, a 429, a 5xx or
an unparseable 200: the empty string was written to disk as a test file, counted
as generated, and the plugin returned ``pass``. A probe reproduced exactly that
— ``STATUS: pass`` over a zero-byte ``test_*_ai.py``.

Three other ways this reported health it had not established:

* an empty ``source_dir``, or one whose every file failed ``ast.parse``, still
  returned ``pass`` with ``count: 0``;
* the ``[:5]`` cap was silent, so five files were read and the whole directory
  was reported on;
* ``total_cost += 0.0001`` was booked per file whether or not a call happened,
  and being flat it could never move the governor — five files against the $5
  default budget is 0.01%, so ``get_model``'s 20%-remaining downgrade could not
  fire however long a run went on.

It now asks ``complete_result()``, which names the failure instead of flattening
it to ``""``, writes nothing when there is no content, prices what was actually
spent, and answers ``unknown`` when it generated nothing at all.
"""

from __future__ import annotations

import ast
import sys

from plugins._base_plugin import (
    BasePlugin,
    PluginPriority,
    PluginResult,
    PluginStatus,
    is_passing,
)
from plugins.cost_governor import CostGovernor, estimate_cost
from plugins.tier1._paths import iter_source_files, resolve_dir

_PREFERRED_MODEL = "claude-haiku-4-5-20251001"

_MAX_FILES = 5
_PROMPT_SOURCE_CHARS = 2000


class UnitAIPlugin(BasePlugin):
    name = "unit-ai"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["source_change"]

    def run(self, context: dict) -> PluginResult:
        if context.get("dry_run"):
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[{"reason": "dry run — no generation attempted"}],
                dry_run=True,
            )

        governor = context.get("cost_governor") or CostGovernor()
        source_dir = resolve_dir(context, "source_dir", ".")
        if not source_dir.is_dir():
            return _unknown(f"source_dir {source_dir} does not exist — nothing was read")

        candidates = [f for f in iter_source_files(source_dir) if "test" not in f.name]
        if not candidates:
            return _unknown(
                f"no non-test Python file under {source_dir} — nothing to generate from"
            )

        model = governor.get_model(_PREFERRED_MODEL)
        try:
            # Imported at call time: the registry loads every plugin file at
            # startup, and this one must not take the framework down with it if
            # the LLM client cannot be imported.
            from api.llm.claude_client import ClaudeLLMClient
        except Exception as exc:  # noqa: BLE001
            return PluginResult(
                status=PluginStatus.ERROR.value,
                findings=[{"error": f"LLM client unavailable: {type(exc).__name__}: {exc}"}],
            )
        llm = ClaudeLLMClient(model=model)

        limit = max(1, int(context.get("max_files", _MAX_FILES)))
        examined = candidates[:limit]

        out_dir = resolve_dir(context, "out_dir", "ai_generated_tests/unit")
        out_dir.mkdir(parents=True, exist_ok=True)

        generated: list[str] = []
        unparseable: list[dict] = []
        outages: list[dict] = []
        total_tokens = 0
        total_cost = 0.0

        for src_file in examined:
            try:
                source = src_file.read_text(encoding="utf-8")
                ast.parse(source)
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
                unparseable.append({"file": str(src_file), "error": f"{type(exc).__name__}: {exc}"})
                continue

            prompt = (
                f"Generate pytest unit tests for this Python module. "
                f"Return only valid Python test code.\n\n"
                f"```python\n{source[:_PROMPT_SOURCE_CHARS]}\n```"
            )
            response, failure = self._complete(governor, llm, prompt)
            if failure is not None:
                outages.append({"file": str(src_file), "llm_failure": failure})
                continue
            if not response.strip():
                # A 200 whose body carries no text arrives here as success with
                # nothing in it. Writing it is what produced the zero-byte test
                # file the probe found, counted as generated.
                outages.append({"file": str(src_file), "llm_failure": "empty response body"})
                continue

            out_file = out_dir / f"test_{src_file.stem}_ai.py"
            out_file.write_text(response, encoding="utf-8")
            generated.append(str(out_file))

            # Reported on the result, not recorded against the budget: the
            # governor now prices and records every call routed through
            # `cached_complete`, and recording here as well would count this
            # spend twice.
            tokens, cost = estimate_cost(prompt, response)
            total_tokens += tokens
            total_cost += cost

        finding = {
            "generated_files": generated,
            "count": len(generated),
            "source_dir": str(source_dir),
            "candidates": len(candidates),
            "examined": len(examined),
            # The cap is a deliberate budget control, but it was invisible: the
            # old finding named only the five files and the status spoke for the
            # whole directory.
            "not_examined": len(candidates) - len(examined),
            "unparseable": unparseable,
            "llm_outages": outages,
        }

        if not generated:
            finding["reason"] = (
                f"no test was generated from {len(examined)} file(s): "
                f"{len(outages)} model outage(s), {len(unparseable)} unparseable source(s)"
            )
            return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[finding])

        status = PluginStatus.WARN.value if (outages or unparseable) else PluginStatus.PASS.value
        return PluginResult(
            status=status,
            findings=[finding],
            tokens_used=total_tokens,
            cost_usd=total_cost,
        )

    @staticmethod
    def _complete(governor: CostGovernor, llm: object, prompt: str) -> tuple[str, str | None]:
        """Return ``(text, failure)`` — the failure named, not flattened to "".

        ``cached_complete`` speaks in strings, and an outage looks like ``""``
        from in there. Capturing the ``Completion``'s failure on the way past is
        what lets the plugin say *which* outage instead of writing the empty
        string out as a test file.
        """
        captured: list[str] = []

        def call(text: str) -> str:
            completion = llm.complete_result(text)  # type: ignore[attr-defined]
            if not completion.ok:
                captured.append(str(completion.failure))
            return completion.text

        response = governor.cached_complete(prompt, call)
        return response, captured[0] if captured else None


def _unknown(reason: str) -> PluginResult:
    return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[{"reason": reason}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = UnitAIPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if is_passing(result.status) or result.status == PluginStatus.SKIP.value else 1)
