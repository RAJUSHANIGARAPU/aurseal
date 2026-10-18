"""Browser examples of the platform UI (a real browser against a real server).

They need the optional web stack (requirements-platform.txt); without it this directory is
not collected at all, so the browser lane neither runs nor skips them.
"""

from __future__ import annotations

import importlib.util

import pytest


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


MISSING = [m for m in ("fastapi", "uvicorn", "jinja2") if not _importable(m)]
collect_ignore_glob = ["*"] if MISSING else []


def pytest_report_header(config):
    if MISSING:
        return f"demo_examples/platform: not collected, missing {', '.join(MISSING)}"
    return None


if not MISSING:
    from gate.integrity import Thresholds
    from qaplatform.cli import bootstrap
    from qaplatform.service import PlatformService
    from qaplatform.store import PlatformStore

    @pytest.fixture
    def platform(tmp_path, monkeypatch):
        """A platform server on a free loopback port and an admin token for it."""
        from qaplatform.http.app import serve_in_thread

        for name in ("RESULTS_DB", "QAP_DB", "QAP_DISPATCHER"):
            monkeypatch.delenv(name, raising=False)
        db = str(tmp_path / "platform.db")
        store = PlatformStore.open(db)
        token, _ = bootstrap(store)
        store.close()
        thresholds = Thresholds(min_executed=1, max_skip_ratio=0.5, min_verdicts=1, min_acquired=0)

        def factory():
            return PlatformService(PlatformStore.open(db), thresholds=thresholds)

        with serve_in_thread(factory) as base_url:
            yield base_url, token
