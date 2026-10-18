"""
Synthetic data edge plugin — sends boundary and invalid records to a target and judges them.

This plugin used to write twenty edge-case profiles and a generated Playwright
test whose body was ``pytest.skip``: fixtures that were never sent anywhere,
and a run that could not reach a verdict. It now does the work the fixtures
existed for. The engine lives in :mod:`test_data.synthetic`; this file only
builds inputs from ``context`` and maps the outcome to a status.

Context:

* ``profile`` — a constraint profile (path, dict or ``Profile``). Required.
* ``target`` — base URL of the system under test. Required; no default is ever
  invented.
* ``endpoint`` — path to POST records to (default: the profile's ``endpoint``).
* ``seed`` (default 0), ``valid_n`` (default 5) — reproducibility and volume.
* ``http`` — an object with ``post(path, json=)``; default ``HttpEngine(target)``.

Status:

=================================================  =========
situation                                          status
=================================================  =========
dry run                                            skip (``dry_run=True``), nothing written
no profile, no target, no endpoint                 unknown
Faker not installed                                unknown (the extremes would be thin)
no records generated                               unknown
target unreachable for any record                  unknown
any 5xx, or an invalid record accepted, or a
valid record rejected                              fail
every record answered as its label says            pass
=================================================  =========

A real run writes ``synthetic_profiles.json`` (records and verdicts, for people)
and one ``metric_synthetic_probe`` row per record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus

_OUT_DIR = Path("ai_generated_tests/synthetic")
_PROFILES_FILE = _OUT_DIR / "synthetic_profiles.json"
_TABLE = "metric_synthetic_probe"


def _unknown(reason: str, **extra) -> PluginResult:
    return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[{"reason": reason, **extra}])


def _load_profile(raw):
    from test_data.synthetic.profile import Profile

    if isinstance(raw, Profile):
        return raw
    if isinstance(raw, dict):
        return Profile.from_dict(raw)
    return Profile.from_json(raw)


class SyntheticDataEdgePlugin(BasePlugin):
    name = "synthetic-data-edge"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        if context.get("dry_run", False):
            # Before anything is generated or written.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[
                    {
                        "would_probe": context.get("target") or "(no target)",
                        "profile": str(context.get("profile") or "(no profile)"),
                        "profiles_file": str(_PROFILES_FILE),
                    }
                ],
                dry_run=True,
            )

        if not context.get("profile"):
            return _unknown("no constraint profile given (context['profile'])")
        target = context.get("target")
        if not target:
            return _unknown("no target given (context['target']); none is invented")

        from test_data.synthetic.generator import SyntheticGenerator
        from test_data.synthetic.probe import FAIL, UNREACHABLE, probe, summarise

        profile = _load_profile(context["profile"])
        endpoint = context.get("endpoint") or profile.endpoint
        if not endpoint:
            return _unknown("no endpoint: set context['endpoint'] or the profile's 'endpoint'")

        generator = SyntheticGenerator(int(context.get("seed", 0)))
        if not generator.faker_available:
            return _unknown(
                "faker is not installed; the generated values would be thin stand-ins, "
                "so this run would not exercise what it exists to exercise",
                faker_available=False,
            )

        records = generator.generate(profile, int(context.get("valid_n", 5)), "valid")
        for mode in ("boundary", "invalid"):
            records += generator.generate(profile, len(generator.cases(profile, mode)), mode)
        if not records:
            return _unknown("the profile produced no records")

        http = context.get("http")
        if http is None:
            from api.clients.http_engine import HttpEngine

            http = HttpEngine(target, max_retries=0)
        results = probe(endpoint, http, records)
        summary = summarise(results)
        findings = {"target": target, "endpoint": endpoint, "profile": profile.entity, **summary}

        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        _PROFILES_FILE.write_text(
            json.dumps(
                [{**r.record.to_dict(), "status": r.status, "verdict": r.verdict} for r in results],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        findings["profiles_file"] = str(_PROFILES_FILE)

        from checks._sink import write_metrics

        problem = write_metrics(_TABLE, [r.metric_row(profile.entity) for r in results])
        if problem:
            findings["metrics"] = problem

        if any(r.verdict == UNREACHABLE for r in results):
            findings["reason"] = "the target did not answer every record; no verdict"
            return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[findings])
        if any(r.verdict == FAIL for r in results):
            return PluginResult(status=PluginStatus.FAIL.value, findings=[findings])
        return PluginResult(status=PluginStatus.PASS.value, findings=[findings])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--target")
    parser.add_argument("--endpoint")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    ctx: dict = {
        k: v
        for k, v in {
            "profile": args.profile,
            "target": args.target,
            "endpoint": args.endpoint,
            "seed": args.seed,
        }.items()
        if v is not None
    }
    plugin = SyntheticDataEdgePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "warn", "skip") else 1)
