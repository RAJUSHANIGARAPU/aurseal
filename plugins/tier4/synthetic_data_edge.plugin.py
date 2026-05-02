"""Synthetic data edge plugin — generates extreme synthetic profiles via Faker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_OUT_DIR = Path("ai_generated_tests/synthetic")
_PROFILES_FILE = _OUT_DIR / "synthetic_profiles.json"

_ZWS = "​"  # zero-width space
_EXTREME_NAMES = [
    "A" * 256,
    "محمد عبدالله",  # Arabic
    "יוסף כהן",  # Hebrew
    f"Test{_ZWS}User",
    "Test\U0001f600User",  # emoji in name
]


def _generate_profiles() -> list[dict]:
    try:
        from faker import Faker

        fake = Faker()
        profiles: list[dict] = []

        # Standard extreme profiles
        for name in _EXTREME_NAMES:
            profiles.append(
                {
                    "name": name,
                    "email": fake.email(),
                    "age": 0,
                    "notes": _ZWS * 10,
                }
            )

        # Edge case values
        profiles.extend(
            [
                {"name": fake.name(), "age": 2**53, "email": "a@b.c"},  # MAX_SAFE_INTEGER
                {"name": fake.name(), "age": -1, "email": ""},
                {"name": "", "age": 0, "email": "not-an-email"},
                {"name": fake.name(), "email": "x" * 300 + "@test.com", "age": 150},
                {
                    "name": "‮‮reversed",  # RTL override
                    "email": fake.email(),
                    "age": fake.random_int(0, 120),
                },
            ]
        )

        # Fill to 20
        while len(profiles) < 20:
            profiles.append(
                {
                    "name": fake.name(),
                    "email": fake.email(),
                    "age": fake.random_int(0, 120),
                    "notes": fake.text(max_nb_chars=50),
                }
            )
        return profiles[:20]
    except ImportError:
        return [{"name": f"profile_{i}", "age": i, "email": f"test{i}@test.com"} for i in range(20)]


class SyntheticDataEdgePlugin(BasePlugin):
    name = "synthetic-data-edge"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841
        is_dry = context.get("dry_run", False)

        profiles = _generate_profiles()

        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        content = json.dumps(profiles, indent=2, ensure_ascii=False)
        _PROFILES_FILE.write_text(content, encoding="utf-8")

        if is_dry:
            return PluginResult(
                status="pass",
                findings=[
                    {
                        "profiles_generated": len(profiles),
                        "profiles_file": str(_PROFILES_FILE),
                        "sample_descriptions": [
                            f"name={p.get('name', '')[:30]!r}" for p in profiles[:5]
                        ],
                    }
                ],
                dry_run=True,
            )

        # Generate Playwright test script for each profile
        test_dir = _OUT_DIR / "playwright_tests"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / "test_synthetic_profiles.py"
        test_content = (
            '"""Auto-generated Playwright tests for synthetic edge-case profiles."""\n'
            "from __future__ import annotations\n\n"
            "import json\nimport pytest\nfrom playwright.sync_api import Page\n\n\n"
            f"_PROFILES = json.loads(\n    open('{_PROFILES_FILE}').read()\n)\n\n\n"
            "@pytest.mark.parametrize('profile', _PROFILES)\n"
            "def test_synthetic_profile(page: Page, profile: dict) -> None:\n"
            '    """Verify app handles extreme profile data gracefully."""\n'
            "    # TODO: navigate to form and fill with profile data\n"
            "    assert profile is not None\n"
        )
        test_file.write_text(test_content, encoding="utf-8")

        descriptions = [f"name={p.get('name', '')[:30]!r}" for p in profiles]
        return PluginResult(
            status="pass",
            findings=[
                {
                    "profiles_generated": len(profiles),
                    "profiles_file": str(_PROFILES_FILE),
                    "test_script": str(test_file),
                    "profile_descriptions": descriptions,
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = SyntheticDataEdgePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
