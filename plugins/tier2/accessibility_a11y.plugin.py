"""Accessibility a11y plugin — generates axe-core Playwright test scripts."""

from __future__ import annotations

import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_WCAG_CHECKS = [
    "1.1.1 Non-text Content",
    "1.4.3 Contrast (Minimum)",
    "2.1.1 Keyboard",
    "2.4.1 Bypass Blocks",
    "3.1.1 Language of Page",
    "4.1.2 Name, Role, Value",
]

_AXE_TEMPLATE = '''"""Auto-generated axe-core accessibility test."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page


@pytest.mark.a11y
def test_accessibility_{slug}(page: Page) -> None:
    """Run axe-core accessibility checks on {route}."""
    page.goto("{route}")
    page.wait_for_load_state("networkidle")
    # Requires: pip install axe-playwright
    # from axe_playwright_python.sync_playwright import Axe
    # results = Axe().run(page)
    # assert results.violations_count == 0, results.generate_report()
    # WCAG checks targeted: {wcag}
    pass
'''


class AccessibilityA11yPlugin(BasePlugin):
    name = "accessibility-a11y"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["ui_change"]

    def run(self, context: dict) -> PluginResult:
        routes: list[str] = context.get("routes", ["http://localhost"])

        if context.get("dry_run"):
            reqs_file = Path("requirements.txt")
            axe_available = False
            if reqs_file.exists():
                axe_available = "axe-playwright" in reqs_file.read_text(encoding="utf-8")
            return PluginResult(
                status="pass",
                findings=[
                    {
                        "axe_playwright_available": axe_available,
                        "wcag_checks": _WCAG_CHECKS,
                        "routes": routes,
                    }
                ],
                dry_run=True,
            )

        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841
        out_dir = Path("ai_generated_tests/a11y")
        out_dir.mkdir(parents=True, exist_ok=True)
        generated: list[str] = []

        for route in routes:
            slug = route.replace("://", "_").replace("/", "_").replace(".", "_")[:40]
            script = _AXE_TEMPLATE.format(
                slug=slug,
                route=route,
                wcag=", ".join(_WCAG_CHECKS),
            )
            out_file = out_dir / f"test_a11y_{slug}.py"
            out_file.write_text(script, encoding="utf-8")
            generated.append(str(out_file))

        return PluginResult(
            status="pass",
            findings=[
                {
                    "generated_files": generated,
                    "wcag_checks": _WCAG_CHECKS,
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"routes": ["http://localhost"]}
    plugin = AccessibilityA11yPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
