"""Load performance plugin — generates a k6 script for real routes and runs it.

Two things used to make this plugin's ``pass`` meaningless.

An empty route list was replaced with ``["http://localhost"]``, so a run that
had been told nothing about the product load-tested a placeholder and reported
health. Nothing downstream could tell that apart from ten VUs against the real
service. There is no honest verdict available without routes, so the plugin now
says so: ``unknown``.

The script it executed was model-generated JavaScript, written straight to disk
and handed to ``k6 run`` with no check beyond "the reply was not empty". There
is no JavaScript parser in this process, so there was no check available — the
only thing standing between an outage message and a subprocess was the prose
sniffing that ``api.llm.base`` exists to abolish. The model path is therefore
gone: the executed script is generated deterministically from the routes. A
load test is a measurement, and a measurement whose instrument cannot be
inspected is not evidence.

What is left can genuinely fail, which makes this the one tier-3 plugin with a
``fail`` to return: k6 exits non-zero when a check or a threshold is breached,
and that is a real finding about the product, not about the harness.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus

_OUT_DIR = Path("ai_generated_tests/load")
_SCRIPT_NAME = "load_test.js"
_RUN_TIMEOUT_S = 120

_K6_TEMPLATE = """import http from 'k6/http';
import {{ check, sleep }} from 'k6';

export const options = {{
  vus: 10,
  duration: '30s',
}};

export default function () {{
  const routes = {routes};
  routes.forEach(url => {{
    const res = http.get(url);
    check(res, {{ 'status is 200': (r) => r.status === 200 }});
  }});
  sleep(1);
}}
"""


def _usable_routes(raw: object) -> tuple[list[str], list[str]]:
    """Split the supplied routes into ones k6 can fetch and ones it cannot.

    Rejected entries are returned rather than dropped: "you gave me four routes
    and none of them had a scheme" is actionable, and "no routes" is not.
    """
    if not isinstance(raw, list | tuple):
        return [], []
    usable: list[str] = []
    rejected: list[str] = []
    for entry in raw:
        candidate = entry.strip() if isinstance(entry, str) else ""
        if candidate.startswith(("http://", "https://")) and len(candidate) > len("https://"):
            usable.append(candidate)
        else:
            rejected.append(repr(entry))
    return usable, rejected


class LoadPerformancePlugin(BasePlugin):
    name = "load-performance"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["pre_deploy", "manual"]

    def _write_script(self, routes: list[str], out_dir: Path) -> str:
        out_dir.mkdir(parents=True, exist_ok=True)
        routes_json = "[" + ", ".join(f'"{r}"' for r in routes) + "]"
        out_file = out_dir / _SCRIPT_NAME
        out_file.write_text(_K6_TEMPLATE.format(routes=routes_json), encoding="utf-8")
        return str(out_file)

    def run(self, context: dict) -> PluginResult:
        routes, rejected = _usable_routes(context.get("routes"))

        if not routes:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": "no usable routes were supplied — nothing was load tested",
                        "rejected_routes": rejected,
                    }
                ],
                dry_run=bool(context.get("dry_run")),
            )

        script_path = self._write_script(routes, Path(context.get("out_dir") or _OUT_DIR))
        base_finding = {"script_path": script_path, "routes": routes}
        if rejected:
            base_finding["rejected_routes"] = rejected

        if context.get("dry_run"):
            return PluginResult(
                status=PluginStatus.PASS.value,
                findings=[base_finding],
                dry_run=True,
            )

        if shutil.which("k6") is None:
            # Not a SKIP. Nobody declared this run exempt from load testing; the
            # tool it needs is simply absent, so the question was asked and went
            # unanswered.
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{**base_finding, "reason": "k6 is not installed — no load was applied"}],
            )

        try:
            proc = subprocess.run(
                ["k6", "run", script_path],
                capture_output=True,
                text=True,
                timeout=_RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            # The load test was cut off part-way. It neither passed nor found a
            # breach — it produced no measurement at all.
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        **base_finding,
                        "reason": f"k6 did not finish within {_RUN_TIMEOUT_S}s",
                    }
                ],
            )
        except OSError as exc:
            return PluginResult(
                status=PluginStatus.ERROR.value,
                findings=[{**base_finding, "error": f"{type(exc).__name__}: {exc}"}],
            )

        output = (proc.stdout + proc.stderr)[-500:]
        if proc.returncode == 0:
            return PluginResult(
                status=PluginStatus.PASS.value,
                findings=[{**base_finding, "exit_code": 0, "output": output}],
            )
        return PluginResult(
            status=PluginStatus.FAIL.value,
            findings=[
                {
                    **base_finding,
                    "exit_code": proc.returncode,
                    "output": output,
                    "reason": "k6 reported failed checks or breached thresholds",
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--route", action="append", default=[], help="repeatable target URL")
    args = parser.parse_args()
    ctx: dict = {"routes": args.route}
    plugin = LoadPerformancePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    # `unknown` exits non-zero: the old exit code treated "learned nothing" as
    # success, which is the same lie the status vocabulary was changed to stop.
    sys.exit(0 if result.status in (PluginStatus.PASS.value, PluginStatus.WARN.value) else 1)
