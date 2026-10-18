"""``--visual-update``: write missing baselines instead of failing on them.

Load with ``-p checks.visual.pytest_plugin`` (or list it in the root conftest's
``pytest_plugins``). The option sets ``VISUAL_UPDATE=1`` for the session, which is what
``assert_matches_baseline`` reads, so the environment variable alone works too.
"""

from __future__ import annotations

import os

import pytest

from checks.visual.snapshot import UPDATE_ENV


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--visual-update",
        action="store_true",
        default=False,
        help="write missing visual baselines (the test is skipped as 'baseline taken')",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--visual-update", default=False):
        os.environ[UPDATE_ENV] = "1"
