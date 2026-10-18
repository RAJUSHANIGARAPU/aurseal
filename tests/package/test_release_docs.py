"""The changelog and the release page agree with the package and the publish workflow."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from testkeel import __version__

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
RELEASING = (ROOT / "docs" / "kit" / "releasing.md").read_text(encoding="utf-8")
PUBLISH = yaml.safe_load((ROOT / ".github" / "workflows" / "publish.yml").read_text("utf-8"))


def _publisher_table() -> dict[str, str]:
    rows = re.findall(r"^\s*\| ([A-Z][^|]+?) \| `([^`]+)` \|$", RELEASING, flags=re.M)
    return dict(rows)


def test_the_changelog_has_an_unreleased_section_and_the_current_version():
    assert "\n## [Unreleased]\n" in CHANGELOG
    assert re.search(
        rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", CHANGELOG, re.M
    )
    assert f"[{__version__}]: " in CHANGELOG


def test_the_pending_publisher_matches_the_workflow():
    table = _publisher_table()
    assert table["PyPI project name"] == "testkeel"
    assert table["Workflow name"] == "publish.yml"
    assert table["Environment name"] == PUBLISH["jobs"]["pypi"]["environment"]["name"]
    assert f"`{PUBLISH['jobs']['testpypi']['environment']['name']}`" in RELEASING
