"""
API contract plugin — runs the contract gate over a configured contract or spec pair.

This plugin used to run the contract library's own unit tests and call that the
API contract check: with no target given, "the library's tests pass" was
published as "the API contract holds". It now runs the real gate
(``contract_testing.gate``) on inputs the caller names, and a missing input is
``unknown`` — nothing was checked, so nothing is known.

Context, one of:

* ``old_spec`` + ``new_spec`` — two OpenAPI documents (paths or dicts);
* ``consumer`` + ``provider`` (+ ``contracts_dir``, default ``contracts`` under the
  repository root) — the two newest stored consumer contracts.

=====================================  =======
gate answer                            status
=====================================  =======
breaking change                        fail
compatible                             pass
no evidence (nothing to compare)       unknown
inputs missing                         unknown
dry run                                skip
=====================================  =======

Each judged run records a ``contract`` verdict and ``metric_contract_change`` rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

from plugins._base_plugin import (
    BasePlugin,
    PluginPriority,
    PluginResult,
    PluginStatus,
    is_passing,
)
from plugins.tier1._paths import resolve_dir

_STATUS = {
    "compatible": PluginStatus.PASS.value,
    "breaking": PluginStatus.FAIL.value,
    "no_evidence": PluginStatus.UNKNOWN.value,
}


class ApiContractPlugin(BasePlugin):
    name = "api-contract"
    priority = PluginPriority.HIGH
    trigger_conditions = ["route_change", "api_change"]

    def run(self, context: dict) -> PluginResult:
        has_specs = bool(context.get("old_spec") and context.get("new_spec"))
        has_pair = bool(context.get("consumer") and context.get("provider"))

        if context.get("dry_run"):
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[
                    {
                        "reason": "dry run — no contract gate executed",
                        "would_check": "openapi" if has_specs else "store" if has_pair else None,
                    }
                ],
                dry_run=True,
            )

        from contract_testing.gate import check_contracts, check_openapi, record

        if has_specs:
            result = check_openapi(context["old_spec"], context["new_spec"])
            consumer = str(context.get("consumer") or "clients")
            provider = str(context.get("provider") or "api")
            source = "openapi"
        elif has_pair:
            from contract_testing.storage import ContractStore

            directory = resolve_dir(context, "contracts_dir", "contracts")
            consumer, provider = str(context["consumer"]), str(context["provider"])
            if not Path(directory).is_dir():
                return _unknown(f"{directory} does not exist — no stored contract to compare")
            result = check_contracts(ContractStore(Path(directory)), consumer, provider)
            source = "capture"
        else:
            return _unknown(
                "no contract inputs: give old_spec + new_spec, or consumer + provider; "
                "nothing was checked"
            )

        findings = {
            "verdict": result.verdict,
            "source": source,
            "breaking": [str(c) for c in result.breaking],
            "non_breaking": len(result.non_breaking),
            "from_version": result.from_version,
            "to_version": result.to_version,
        }
        if result.reason:
            findings["reason"] = result.reason
        problem = record(result, consumer=consumer, provider=provider, source=source)
        if problem:
            findings["metrics"] = problem
        return PluginResult(status=_STATUS[result.verdict], findings=[findings])


def _unknown(reason: str) -> PluginResult:
    return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[{"reason": reason}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--old-spec")
    parser.add_argument("--new-spec")
    parser.add_argument("--consumer")
    parser.add_argument("--provider")
    args = parser.parse_args()
    ctx: dict = {
        k: v
        for k, v in {
            "old_spec": args.old_spec,
            "new_spec": args.new_spec,
            "consumer": args.consumer,
            "provider": args.provider,
        }.items()
        if v
    }
    plugin = ApiContractPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if is_passing(result.status) or result.status == PluginStatus.SKIP.value else 1)
