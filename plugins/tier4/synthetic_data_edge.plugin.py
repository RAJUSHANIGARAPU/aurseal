"""
Synthetic data edge plugin — generates extreme profiles and a stub to feed them.

Three things this had to stop doing.

**A dry run wrote files.** ``_PROFILES_FILE.write_text(...)`` sat four lines
above the ``if is_dry:`` branch, so ``--dry-run`` created
``ai_generated_tests/synthetic/`` and overwrote the profiles on disk before
returning its "nothing was changed" preview. Every sibling plugin guards this;
this one did the write first and then reported it as a plan.

**Its generated test asserted nothing.** ``assert profile is not None`` over a
parametrised list of dicts is true twenty times out of twenty. Twenty green
tests appeared, named after edge cases nobody had sent anywhere. The stub now
skips, which is the truthful state of a test that has no target yet.

**A missing Faker was invisible.** The ``except ImportError`` fallback produces
``profile_0 … profile_19`` — no long names, no RTL override, no zero-width
characters, none of the extremes this plugin exists to produce — and the result
said ``profiles_generated: 20`` exactly as it does on the good path. That case
is now ``unknown`` and says why.

The run itself returns ``skip``: writing fixtures is not a verdict about the
health of the product, and the old ``pass`` put it into the score as though it
were.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus

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

_TEST_TEMPLATE = '''"""Playwright stubs for synthetic edge-case profiles, generated.

The generated body is a skip, not an assertion. A stub with no target must not
report twenty green tests over profiles that were never sent anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page

_PROFILES = json.loads(Path({profiles_file!r}).read_text(encoding="utf-8"))


@pytest.mark.parametrize("profile", _PROFILES)
def test_synthetic_profile(page: Page, profile: dict) -> None:
    """Fill the form with an extreme profile — not implemented."""
    pytest.skip("TODO: navigate to the form and fill it with profile data")
'''


def _generate_profiles() -> tuple[list[dict], bool]:
    """Twenty profiles, and whether Faker was there to make them extreme."""
    try:
        from faker import Faker
    except ImportError:
        return (
            [{"name": f"profile_{i}", "age": i, "email": f"test{i}@test.com"} for i in range(20)],
            False,
        )

    fake = Faker()
    profiles: list[dict] = [
        {"name": name, "email": fake.email(), "age": 0, "notes": _ZWS * 10}
        for name in _EXTREME_NAMES
    ]
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
    while len(profiles) < 20:
        profiles.append(
            {
                "name": fake.name(),
                "email": fake.email(),
                "age": fake.random_int(0, 120),
                "notes": fake.text(max_nb_chars=50),
            }
        )
    return profiles[:20], True


class SyntheticDataEdgePlugin(BasePlugin):
    name = "synthetic-data-edge"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        is_dry = context.get("dry_run", False)

        profiles, faker_available = _generate_profiles()
        descriptions = [f"name={p.get('name', '')[:30]!r}" for p in profiles]

        if is_dry:
            # Before any write. A dry run that has already replaced the profiles
            # file is not a dry run, whatever it returns.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[
                    {
                        "profiles_generated": len(profiles),
                        "profiles_file": str(_PROFILES_FILE),
                        "faker_available": faker_available,
                        "sample_descriptions": descriptions[:5],
                    }
                ],
                dry_run=True,
            )

        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        _PROFILES_FILE.write_text(
            json.dumps(profiles, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        test_dir = _OUT_DIR / "playwright_tests"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / "test_synthetic_profiles.py"
        test_file.write_text(
            _TEST_TEMPLATE.format(profiles_file=str(_PROFILES_FILE)), encoding="utf-8"
        )

        findings = {
            "profiles_generated": len(profiles),
            "profiles_file": str(_PROFILES_FILE),
            "test_script": str(test_file),
            "faker_available": faker_available,
            "profile_descriptions": descriptions,
        }

        if not faker_available:
            findings["reason"] = (
                "faker is not installed; the fallback profiles carry no edge cases, so "
                "this run produced fixtures that do not exercise what it exists to exercise"
            )
            return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[findings])

        # Writing fixtures is not a verdict about the product. SKIP keeps it out
        # of the health score instead of filling the score with a file write.
        findings["reason"] = (
            f"wrote {len(profiles)} edge-case profiles and a parametrised stub; "
            "nothing was exercised, so this run carries no verdict"
        )
        return PluginResult(status=PluginStatus.SKIP.value, findings=[findings])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = SyntheticDataEdgePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "warn", "skip") else 1)
