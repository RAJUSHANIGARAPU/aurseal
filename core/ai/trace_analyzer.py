"""Reads a Playwright trace ZIP and uses Claude to describe what happened."""

import json
import os
import zipfile
from pathlib import Path

import requests


class TraceAnalyzer:
    """Extracts actions and failures from a Playwright trace ZIP via Claude API."""

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = "claude-sonnet-4-6"

    def find_latest_trace(self, traces_dir: str = "reports/traces") -> Path | None:
        traces = sorted(
            Path(traces_dir).glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        return traces[0] if traces else None

    def extract_trace_summary(self, trace_path: Path) -> dict:
        """Extract key info from trace ZIP without full parsing."""
        actions = []
        network_errors = []
        console_errors = []

        try:
            with zipfile.ZipFile(trace_path, "r") as z:
                names = z.namelist()
                trace_files = [n for n in names if n.endswith(".trace") or n == "trace.json"]

                for tf in trace_files[:1]:
                    with z.open(tf) as f:
                        for line in f:
                            try:
                                event = json.loads(line.strip())
                                etype = event.get("type", "")
                                if etype in ("action", "event"):
                                    actions.append(event)
                                if event.get("method") == "Network.responseReceived":
                                    status = (
                                        event.get("params", {}).get("response", {}).get("status", 0)
                                    )
                                    if status >= 400:
                                        network_errors.append(event)
                                if event.get("method") == "Runtime.consoleAPICalled":
                                    if event.get("params", {}).get("type") == "error":
                                        console_errors.append(event)
                            except (json.JSONDecodeError, KeyError):
                                pass
        except Exception as e:
            return {"error": str(e), "actions": [], "network_errors": [], "console_errors": []}

        return {
            "trace_file": str(trace_path),
            "total_actions": len(actions),
            "network_errors": len(network_errors),
            "console_errors": len(console_errors),
            "last_actions": actions[-10:] if actions else [],
            "network_error_details": network_errors[:5],
        }

    def analyze(self, trace_path: Path | None = None) -> str:
        """Analyze a trace ZIP and return a human-readable report."""
        if trace_path is None:
            trace_path = self.find_latest_trace()
        if trace_path is None:
            return "No trace files found in reports/traces/"

        summary = self.extract_trace_summary(trace_path)

        if not self.api_key:
            return (
                f"Trace: {summary['trace_file']}\n"
                f"Actions: {summary['total_actions']}\n"
                f"Network errors: {summary['network_errors']}\n"
                f"Console errors: {summary['console_errors']}\n"
                "(Set ANTHROPIC_API_KEY for AI analysis)"
            )

        prompt = f"""You are a Playwright test automation expert.
Analyzing a trace from a wiki UI test.

Trace summary:
{json.dumps(summary, indent=2, default=str)}

Analyze this trace and provide:
1. What actions were performed (navigation, clicks, fills)
2. Any failures or errors encountered
3. Root cause if there was a failure
4. Specific fix suggestion for the Page Object or test code

Be concise and actionable. Focus on what went wrong and exactly how to fix it."""

        return self._call_claude(prompt)

    def _call_claude(self, prompt: str) -> str:
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        except Exception as e:
            return f"Claude API error: {e}"
