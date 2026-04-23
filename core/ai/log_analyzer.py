"""Reads pytest output / logs and identifies failures with root causes."""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import requests


class LogAnalyzer:
    """Parses pytest logs and JUnit XML reports, then uses Claude to diagnose failures."""

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = "claude-sonnet-4-6"

    def find_junit_reports(self, reports_dir: str = "reports") -> list[Path]:
        return list(Path(reports_dir).glob("**/*.xml"))

    def parse_failures(self, xml_path: Path) -> list[dict]:
        failures = []
        try:
            tree = ET.parse(xml_path)
            for tc in tree.iter("testcase"):
                for child in tc:
                    if child.tag in ("failure", "error"):
                        failures.append(
                            {
                                "test": tc.attrib.get("name", ""),
                                "classname": tc.attrib.get("classname", ""),
                                "type": child.attrib.get("type", child.tag),
                                "message": child.attrib.get("message", ""),
                                "text": (child.text or "")[:2000],
                            }
                        )
        except Exception as e:
            failures.append({"error": str(e), "file": str(xml_path)})
        return failures

    def read_log_tail(self, log_path: str = "reports/logs/test.log", lines: int = 100) -> str:
        try:
            with open(log_path) as f:
                all_lines = f.readlines()
            return "".join(all_lines[-lines:])
        except FileNotFoundError:
            return ""

    def analyze_failures(self, reports_dir: str = "reports") -> str:
        """Analyze all test failures and return diagnosis + fix suggestions."""
        all_failures = []
        for xml_path in self.find_junit_reports(reports_dir):
            all_failures.extend(self.parse_failures(xml_path))

        if not all_failures:
            log_tail = self.read_log_tail()
            if not log_tail:
                return "No failures found and no log file available."
            all_failures = [{"log_tail": log_tail}]

        if not self.api_key:
            return (
                f"Found {len(all_failures)} failure(s):\n"
                + "\n".join(
                    f"  - {f.get('test', 'unknown')}: {f.get('message', '')}" for f in all_failures
                )
                + "\n(Set ANTHROPIC_API_KEY for AI diagnosis)"
            )

        log_tail = self.read_log_tail()

        prompt = f"""You are a Playwright + pytest expert analyzing test failures.

FAILURES:
{json.dumps(all_failures, indent=2)}

RECENT LOG (last 100 lines):
{log_tail}

For each failure:
1. State the root cause in one sentence
2. Give the exact code fix (file path + line if identifiable)
3. Classify: selector issue / timing issue / assertion issue / data issue / environment issue

Read docs/ai_learnings.md patterns if available. Be concise and actionable."""

        return self._call_claude(prompt, max_tokens=2048)

    def _call_claude(self, prompt: str, max_tokens: int = 1024) -> str:
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
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        except Exception as e:
            return f"Claude API error: {e}"
