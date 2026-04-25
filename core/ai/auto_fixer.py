"""Applies AI-suggested code fixes to Python test files."""

import os
import re
from pathlib import Path

import requests


class AutoFixer:
    """Uses Claude to generate and apply targeted code fixes to page objects and tests."""

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = "claude-sonnet-4-6"

    def fix_file(self, file_path: str, error_description: str, error_trace: str = "") -> bool:
        """Read a file, ask Claude to fix it, write the fixed version back.

        Returns True if the file was changed.
        """
        path = Path(file_path)
        if not path.exists():
            print(f"File not found: {file_path}")
            return False

        original = path.read_text()

        if not self.api_key:
            print("ANTHROPIC_API_KEY not set — skipping AI fix")
            return False

        ai_learnings = self._read_learnings()

        prompt = f"""You are a Playwright Python test automation expert.

Fix the following file to resolve the error described below.

FILE: {file_path}
```python
{original}
```

ERROR: {error_description}

STACK TRACE:
{error_trace}

KNOWN PATTERNS (from docs/ai_learnings.md):
{ai_learnings}

Rules:
- Return ONLY the complete fixed Python file content
- No explanation, no markdown fences, no extra text
- Preserve all existing logic; only change what is broken
- Use Playwright best practices: locator chaining, expect() assertions, proper waits
- Never use time.sleep() — use page.wait_for_load_state() or expect(locator).to_be_visible()"""

        fixed = self._call_claude(prompt, max_tokens=4096)

        if not fixed or fixed.startswith("Claude API error"):
            print(f"AI fix failed: {fixed}")
            return False

        # Strip markdown fences if Claude included them despite instructions
        fixed = re.sub(r"^```python\n?", "", fixed.strip())
        fixed = re.sub(r"\n?```$", "", fixed.strip())

        if fixed == original:
            print("AI returned unchanged content — no fix applied")
            return False

        path.write_text(fixed)
        print(f"Fixed: {file_path}")
        return True

    def suggest_fix(
        self, error_description: str, error_trace: str, context_file: str | None = None
    ) -> str:
        """Return a fix suggestion without applying it."""
        context = ""
        if context_file:
            try:
                context = Path(context_file).read_text()
            except FileNotFoundError:
                pass

        ai_learnings = self._read_learnings()

        prompt = f"""You are a Playwright Python test automation expert.

Diagnose this failure and suggest an exact code fix.

ERROR: {error_description}
STACK TRACE: {error_trace}

RELEVANT FILE:
```python
{context}
```

KNOWN PATTERNS:
{ai_learnings}

Provide:
1. Root cause (one sentence)
2. Exact code change (before/after)
3. File path and approximate line number"""

        return self._call_claude(prompt)

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
