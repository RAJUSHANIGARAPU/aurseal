"""Tests of the installable ``testkeel`` package (``src/testkeel``).

The package is installed with ``pip install -e .``. Without it this directory is not
collected at all (one header line says so), so a checkout that only installed
requirements.txt neither runs nor fails these tests. ``src`` is never put on the path:
the tests must exercise the installed package, not the source tree.
"""

from __future__ import annotations

import importlib.util


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


MISSING = not _importable("testkeel")
collect_ignore_glob = ["*"] if MISSING else []


def pytest_report_header(config):
    if MISSING:
        return "package: not collected, testkeel is not installed (pip install -e .)"
    return None
