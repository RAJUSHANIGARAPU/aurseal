"""Generates Playwright Python tests from trace ZIPs or page descriptions."""

import json
import os
import zipfile
from pathlib import Path

import requests


class TestGenerator:
    """Uses Claude to generate complete pytest + Playwright test files from traces."""

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = "claude-sonnet-4-6"

    def generate_from_trace(self, trace_path: Path | None = None, page_name: str = "wiki") -> str:
        """Generate a complete pytest test file from a Playwright trace ZIP."""
        if trace_path is None:
            traces = sorted(
                Path("reports/traces").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            trace_path = traces[0] if traces else None

        if trace_path is None:
            return "# No trace file found"

        actions = self._extract_actions(trace_path)
        ai_learnings = self._read_learnings()

        if not self.api_key:
            return (
                f"# ANTHROPIC_API_KEY not set\n"
                f"# Trace: {trace_path}\n"
                f"# Actions found: {len(actions)}"
            )

        prompt = f"""You are a senior Playwright Python test automation engineer.

Generate a complete, production-quality pytest test file from this Playwright trace.

TRACE FILE: {trace_path.name}
PAGE/FEATURE: {page_name}

RECORDED ACTIONS:
{json.dumps(actions[:50], indent=2, default=str)}

KNOWN PATTERNS (from docs/ai_learnings.md):
{ai_learnings}

FRAMEWORK CONTEXT:
- Test framework: pytest + playwright-pytest
- Base page: core/base_page.py (BasePage class with resolve_locator, click, fill, etc.)
- Locator strategy: data-testid > aria-label > role > text (never CSS classes)
- Config: core/config_reader.py (ConfigReader class)
- Assertions: use expect() from playwright.sync_api
- Never use time.sleep() — use page.wait_for_load_state()
  or expect(locator).to_be_visible(timeout=N)

OUTPUT REQUIREMENTS:
- Package: ui/tests/
- File naming: test_{page_name}.py
- Class: Test{page_name.title().replace("_", "")}
- Each test method: descriptive name starting with test_
- Use @pytest.mark.parametrize where useful
- Include proper fixtures: page, config
- Add docstrings explaining what each test verifies

Return ONLY the complete Python file. No explanation. No markdown fences."""

        return self._call_claude(prompt, max_tokens=4096)

    def generate_page_object(self, page_name: str, page_url: str, page_description: str) -> str:
        """Generate a Page Object class for a given page."""
        ai_learnings = self._read_learnings()

        if not self.api_key:
            return f"# ANTHROPIC_API_KEY not set — cannot generate page object for {page_name}"

        prompt = f"""You are a senior Playwright Python test automation engineer.

Generate a complete Page Object class for this page.

PAGE NAME: {page_name}
URL PATTERN: {page_url}
DESCRIPTION: {page_description}

KNOWN PATTERNS:
{ai_learnings}

FRAMEWORK:
- Inherit from: core.base_page.BasePage
- Constructor: super().__init__(page, config)
- Locator methods: self.resolve_locator(key) from BasePage
- All action methods return self (fluent interface)
- Use data-testid > aria-label > role > text selectors

Return ONLY the complete Python file. No explanation. No markdown fences."""

        return self._call_claude(prompt, max_tokens=2048)

    def _extract_actions(self, trace_path: Path) -> list:
        actions = []
        try:
            with zipfile.ZipFile(trace_path, "r") as z:
                trace_files = [n for n in z.namelist() if n.endswith(".trace") or n == "trace.json"]
                for tf in trace_files[:1]:
                    with z.open(tf) as f:
                        for line in f:
                            try:
                                event = json.loads(line.strip())
                                if event.get("type") == "action":
                                    actions.append(
                                        {
                                            "action": event.get("apiName", ""),
                                            "selector": event.get("params", {}).get("selector", ""),
                                            "value": event.get("params", {}).get("value", ""),
                                            "url": event.get("params", {}).get("url", ""),
                                        }
                                    )
                            except (json.JSONDecodeError, KeyError):
                                pass
        except Exception:
            pass
        return actions

    def _read_learnings(self) -> str:
        try:
            return Path("docs/ai_learnings.md").read_text()
        except FileNotFoundError:
            return "(no learnings file found)"

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
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        except Exception as e:
            return f"Claude API error: {e}"
