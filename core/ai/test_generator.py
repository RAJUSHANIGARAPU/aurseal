"""Generates Playwright Python tests from trace ZIPs, spec files or page descriptions."""

import json
import os
import zipfile
from pathlib import Path

import requests


class TestGenerator:
    """
    Uses Claude to generate complete pytest + Playwright test files.

    Every generation entry point returns ``None`` when it cannot produce code —
    no trace, no API key, an unreadable spec, or an API error. It never returns
    a message dressed up as source. A returned string used to be the only
    channel for both, so ``# No trace file found`` and ``Claude API error: ...``
    were indistinguishable from a generated file, and the callers wrote them
    over real test files and reported success. ``AutoFixer.fix_file`` already
    signals the same conditions with a falsy return; this follows it.
    """

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = "claude-sonnet-4-6"

    def generate_from_trace(
        self, trace_path: Path | None = None, page_name: str = "wiki"
    ) -> str | None:
        """Generate a complete pytest test file from a Playwright trace ZIP.

        Returns the generated source, or None if nothing could be generated.
        """
        if trace_path is None:
            traces = sorted(
                Path("reports/traces").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            trace_path = traces[0] if traces else None

        if trace_path is None:
            print("No trace file found — cannot generate a test")
            return None

        actions = self._extract_actions(trace_path)
        ai_learnings = self._read_learnings()

        if not self.api_key:
            print(f"ANTHROPIC_API_KEY not set — cannot generate a test from {trace_path}")
            return None

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

    def generate_from_spec(self, spec_path: Path, page_name: str = "") -> str | None:
        """Generate a complete pytest test file from a spec markdown file.

        Returns the generated source, or None if the spec cannot be read or the
        API call fails.
        """
        spec_path = Path(spec_path)
        try:
            spec = spec_path.read_text()
        except OSError as e:
            print(f"Spec not readable: {spec_path} ({e})")
            return None

        if not spec.strip():
            print(f"Spec is empty: {spec_path}")
            return None

        page_name = page_name or spec_path.stem.replace("-", "_")
        ai_learnings = self._read_learnings()

        if not self.api_key:
            print(f"ANTHROPIC_API_KEY not set — cannot generate a test from {spec_path}")
            return None

        prompt = f"""You are a senior Playwright Python test automation engineer.

Generate a complete, production-quality pytest test file from this test plan spec.

SPEC FILE: {spec_path.name}
PAGE/FEATURE: {page_name}

SPEC CONTENTS:
{spec}

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
- One test method per "## Scenario" section in the spec
- Class: Test{page_name.title().replace("_", "")}
- Each test method: descriptive name starting with test_, derived from the scenario name
- Turn each "### Steps" entry into an action and each "### Expected" bullet into an assertion
- Apply the spec's "### Tags" as @pytest.mark markers
- Include proper fixtures: page, config
- Add docstrings explaining what each test verifies

Return ONLY the complete Python file. No explanation. No markdown fences."""

        return self._call_claude(prompt, max_tokens=4096)

    def generate_page_object(
        self, page_name: str, page_url: str, page_description: str
    ) -> str | None:
        """Generate a Page Object class for a given page.

        Returns the generated source, or None if it could not be generated.
        """
        ai_learnings = self._read_learnings()

        if not self.api_key:
            print(f"ANTHROPIC_API_KEY not set — cannot generate page object for {page_name}")
            return None

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

    def _call_claude(self, prompt: str, max_tokens: int = 1024) -> str | None:
        """Return the model's text, or None if the call failed.

        A failure must not come back as a string: the callers write what they
        get straight to a .py file, so an error message would land on disk as
        the generated test.
        """
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
            text = resp.json()["content"][0]["text"]
        except Exception as e:
            print(f"Claude API error: {e}")
            return None
        return text or None
