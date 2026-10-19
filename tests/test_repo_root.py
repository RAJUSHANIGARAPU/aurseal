"""The repository root is a directory on the path, never a package.

With an ``__init__.py`` at the root, pytest names every module after the checkout directory
(``<checkout>.conftest``, ``<checkout>.tests...``). A checkout called ``aurseal`` then
collides with the installed ``aurseal`` package and collection stops before any test runs.
Why: docs/ci-decisions.md#repository-root
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_repository_root_has_no_init_file():
    assert not (ROOT / "__init__.py").exists(), (
        "the repository root must not be a package: pytest would import it under the "
        "checkout directory's name"
    )


def test_the_root_conftest_is_imported_independent_of_the_checkout_name(
    request: pytest.FixtureRequest,
):
    root_conftest = ROOT / "conftest.py"
    names = [
        plugin.__name__
        for plugin in request.config.pluginmanager.get_plugins()
        if Path(getattr(plugin, "__file__", "") or "").resolve() == root_conftest
    ]
    assert names == ["conftest"], names
