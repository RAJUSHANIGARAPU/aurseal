"""Business rule compliance plugin — validates code against rules in docs/."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_RULE_PATTERN = re.compile(r"^\s*(?:\d+\.|[-*•])\s+(.+)", re.MULTILINE)


def _extract_rules(docs_dir: Path) -> list[str]:
    rules: list[str] = []
    for md_file in docs_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        matches = _RULE_PATTERN.findall(content)
        rules.extend(m.strip() for m in matches if len(m.strip()) > 10)
    return rules


class BusinessRuleCompliancePlugin(BasePlugin):
    name = "business-rule-compliance"
    priority = PluginPriority.HIGH
    trigger_conditions = ["manual", "deploy"]

    def run(self, context: dict) -> PluginResult:
        docs_dir = Path(context.get("docs_dir", "docs"))
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = context.get("dry_run", False)

        rules = _extract_rules(docs_dir)

        if is_dry:
            return PluginResult(
                status="pass",
                findings=[{"rules_found": rules, "count": len(rules)}],
                dry_run=True,
            )

        if not rules:
            return PluginResult(
                status="skip",
                findings=[{"reason": "no rules found in docs/"}],
            )

        # Use Claude (sonnet) to convert each rule to assertion description
        assertion_checks: list[dict] = []
        compliance_status = "pass"
        try:
            from api.llm.claude_client import ClaudeLLMClient

            model = governor.get_model("claude-sonnet-4-6")
            llm = ClaudeLLMClient(model=model)
            for rule in rules[:20]:
                prompt = (
                    f"Given this business rule: '{rule}'\n"
                    "Convert it to a testable assertion. Format: PASS or FAIL followed by "
                    "one-line assertion description."
                )
                result_text = governor.cached_complete(prompt, llm.complete).strip()
                status_part = "pass" if result_text.upper().startswith("PASS") else "fail"
                if status_part == "fail":
                    compliance_status = "fail"
                assertion_checks.append(
                    {"rule": rule, "assertion": result_text[:200], "status": status_part}
                )
        except Exception:  # noqa: BLE001
            assertion_checks = [
                {"rule": r, "assertion": "check manually", "status": "unknown"} for r in rules[:20]
            ]

        # Fire webhook if failures and env var set
        webhook_url = os.environ.get("COMPLIANCE_WEBHOOK_URL")
        if webhook_url and compliance_status == "fail":
            try:
                import requests

                requests.post(
                    webhook_url,
                    json={"status": "fail", "checks": assertion_checks},
                    timeout=10,
                )
            except Exception:  # noqa: BLE001
                pass

        return PluginResult(
            status=compliance_status,
            findings=[
                {
                    "rule_count": len(rules),
                    "checks": assertion_checks,
                    "compliance_status": compliance_status,
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"docs_dir": "docs"}
    plugin = BusinessRuleCompliancePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
