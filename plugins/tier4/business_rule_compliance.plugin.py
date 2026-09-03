"""
Business rule compliance plugin — reads the rules written in ``docs/``.

**What this plugin does not do.** It never sees the code. It pulls rule-shaped
lines out of the markdown under ``docs/`` and asks a model, one rule at a time,
whether that sentence can be turned into a mechanical assertion. The old prompt
asked for "PASS or FAIL", and a model told only the rule and never the
implementation cannot answer that — so a ``PASS`` was a guess about a codebase
nobody had shown it, reported as compliance. The question asked here is the one
the input can actually settle: is this rule testable as written.

Three separate false verdicts came out of the old version:

- **An exception left the verdict at "pass".** ``compliance_status`` was
  initialised to ``"pass"`` before the try, and the handler rewrote every check
  to ``"unknown"`` without touching it. So the run where the model could not be
  constructed at all — no key, no network — reported twenty unknown checks and a
  green status.
- **An outage read as twenty violations.** ``complete()`` returns ``""`` when
  the model was not reached; ``"".upper().startswith("PASS")`` is False, so
  every rule fell to ``fail``.
- **And then it posted that.** ``COMPLIANCE_WEBHOOK_URL`` fired on exactly that
  path, so a five-minute outage sent a real notification claiming twenty
  compliance failures that nobody had observed. The webhook now carries only
  checks the model actually answered, and never fires on an unknown.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus
from plugins.cost_governor import CostGovernor

logger = logging.getLogger(__name__)

_RULE_PATTERN = re.compile(r"^\s*(?:\d+\.|[-*•])\s+(.+)", re.MULTILINE)

_MAX_RULES = 20

#: Said plainly in every result, because the plugin's own name overstates it.
_SCOPE = (
    "rules are assessed for testability only; no source code was read or "
    "compared against them"
)


def _extract_rules(docs_dir: Path) -> list[str]:
    rules: list[str] = []
    for md_file in sorted(docs_dir.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        matches = _RULE_PATTERN.findall(content)
        rules.extend(m.strip() for m in matches if len(m.strip()) > 10)
    return rules


def _classify(reply: str) -> str:
    """Read one model reply, refusing to invent a verdict from an unusable one.

    Only the two words asked for count. Everything else — the empty string of an
    outage, an error banner, a refusal, a rambling answer — is ``unknown``.
    """
    head = (reply or "").strip().upper()
    if head.startswith("TESTABLE"):
        return "testable"
    if head.startswith("UNTESTABLE"):
        return "untestable"
    return "unknown"


class BusinessRuleCompliancePlugin(BasePlugin):
    name = "business-rule-compliance"
    priority = PluginPriority.HIGH
    trigger_conditions = ["manual", "deploy"]

    def run(self, context: dict) -> PluginResult:
        docs_dir = Path(context.get("docs_dir", "docs"))
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = context.get("dry_run", False)

        if not docs_dir.is_dir():
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"reason": f"docs_dir does not exist: {docs_dir}", "scope": _SCOPE}],
                dry_run=is_dry,
            )

        rules = _extract_rules(docs_dir)

        if is_dry:
            # A dry run is a preview of the work, not the work. SKIP keeps it
            # out of the health score; the old "pass" filled the score with a
            # run that had asked the model nothing.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[{"rules_found": rules, "count": len(rules), "scope": _SCOPE}],
                dry_run=True,
            )

        if not rules:
            # An empty input, not an inapplicable one. The plugin's whole
            # premise is that docs/ states the rules; if it states none, this
            # run learned nothing rather than having nothing to learn.
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"reason": f"no rule-shaped lines found under {docs_dir}"}],
            )

        checks, reachable = self._assess(rules[:_MAX_RULES], governor)

        untestable = [c for c in checks if c["status"] == "untestable"]
        unknown = [c for c in checks if c["status"] == "unknown"]

        if untestable:
            # A found problem outranks an unanswered question: the untestable
            # rules were observed, and the unknown count travels with them so
            # the reader knows the sweep was partial.
            status = PluginStatus.FAIL.value
        elif unknown:
            status = PluginStatus.UNKNOWN.value
        else:
            status = PluginStatus.PASS.value

        if status == PluginStatus.FAIL.value:
            self._notify(untestable, len(unknown))

        return PluginResult(
            status=status,
            findings=[
                {
                    "rule_count": len(rules),
                    "rules_assessed": len(checks),
                    "checks": checks,
                    "testable": len(checks) - len(untestable) - len(unknown),
                    "untestable": len(untestable),
                    "unknown": len(unknown),
                    "model_reachable": reachable,
                    "scope": _SCOPE,
                }
            ],
        )

    def _assess(self, rules: list[str], governor: CostGovernor) -> tuple[list[dict], bool]:
        """Ask the model about each rule. Returns the checks and whether it answered.

        Every failure mode lands on ``unknown``, and the client is built once
        outside the loop so a missing key produces one honest "not reachable"
        rather than twenty identical stack traces.
        """
        try:
            from api.llm.claude_client import ClaudeLLMClient

            llm = ClaudeLLMClient(model=governor.get_model("claude-sonnet-4-6"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] model unreachable: %s", self.name, exc)
            return (
                [
                    {"rule": r, "assertion": "", "status": "unknown", "reason": str(exc)}
                    for r in rules
                ],
                False,
            )

        checks: list[dict] = []
        answered = False
        for rule in rules:
            prompt = (
                f"Given this business rule: '{rule}'\n"
                "Can it be expressed as a mechanical assertion over code or data? "
                "Reply with TESTABLE or UNTESTABLE, then a one-line assertion "
                "description."
            )
            try:
                reply = governor.cached_complete(prompt, llm.complete).strip()
            except Exception as exc:  # noqa: BLE001
                checks.append(
                    {"rule": rule, "assertion": "", "status": "unknown", "reason": str(exc)}
                )
                continue
            verdict = _classify(reply)
            answered = answered or verdict != "unknown"
            checks.append({"rule": rule, "assertion": reply[:200], "status": verdict})
        return checks, answered

    def _notify(self, untestable: list[dict], unknown_count: int) -> None:
        """Post only what was observed.

        The payload names the untestable rules and states how many rules got no
        answer at all, so the notification cannot be read as a verdict over the
        whole set.
        """
        webhook_url = os.environ.get("COMPLIANCE_WEBHOOK_URL")
        if not webhook_url:
            return
        try:
            import requests

            requests.post(
                webhook_url,
                json={
                    "status": "fail",
                    "untestable_rules": untestable,
                    "rules_without_an_answer": unknown_count,
                    "scope": _SCOPE,
                },
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] compliance webhook failed: %s", self.name, exc)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"docs_dir": "docs"}
    plugin = BusinessRuleCompliancePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "warn", "skip") else 1)
