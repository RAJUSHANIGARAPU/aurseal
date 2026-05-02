"""Load performance plugin — generates and optionally runs k6 load test scripts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

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


class LoadPerformancePlugin(BasePlugin):
    name = "load-performance"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["pre_deploy", "manual"]

    def _generate_script(self, routes: list[str], out_dir: Path) -> str:
        out_dir.mkdir(parents=True, exist_ok=True)
        routes_json = str(routes).replace("'", '"')
        script = _K6_TEMPLATE.format(routes=routes_json)
        out_file = out_dir / "load_test.js"
        out_file.write_text(script, encoding="utf-8")
        return str(out_file)

    def run(self, context: dict) -> PluginResult:
        routes: list[str] = context.get("routes", [])
        out_dir = Path("ai_generated_tests/load")
        governor = context.get("cost_governor") or CostGovernor()

        if not routes:
            routes = ["http://localhost"]

        # Try Claude for enhanced script
        try:
            from api.llm.claude_client import ClaudeLLMClient

            model = governor.get_model("claude-haiku-4-5-20251001")
            llm = ClaudeLLMClient(model=model)
            prompt = (
                f"Generate a k6 load test script for these routes: {routes}. "
                "Return only valid JavaScript k6 code."
            )
            response = governor.cached_complete(prompt, llm.complete)
            if response and not response.startswith("Claude API error"):
                out_dir.mkdir(parents=True, exist_ok=True)
                script_path = str(out_dir / "load_test.js")
                Path(script_path).write_text(response, encoding="utf-8")
            else:
                script_path = self._generate_script(routes, out_dir)
        except Exception:  # noqa: BLE001
            script_path = self._generate_script(routes, out_dir)

        if context.get("dry_run"):
            return PluginResult(
                status="pass",
                findings=[{"script_path": script_path, "routes": routes}],
                dry_run=True,
            )

        # Check if k6 is installed
        try:
            proc = subprocess.run(["which", "k6"], capture_output=True, text=True)
            k6_available = proc.returncode == 0
        except Exception:  # noqa: BLE001
            k6_available = False

        if not k6_available:
            return PluginResult(
                status="skip",
                findings=[{"script_path": script_path, "k6_installed": False}],
            )

        try:
            proc = subprocess.run(
                ["k6", "run", script_path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            passed = proc.returncode == 0
            return PluginResult(
                status="pass" if passed else "fail",
                findings=[
                    {
                        "script_path": script_path,
                        "exit_code": proc.returncode,
                        "output": (proc.stdout + proc.stderr)[-500:],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            return PluginResult(status="error", findings=[{"error": str(exc)}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"routes": ["http://localhost"]}
    plugin = LoadPerformancePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
