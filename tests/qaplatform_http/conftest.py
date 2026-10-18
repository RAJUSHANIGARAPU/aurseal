"""Platform tests that need the optional web stack (requirements-platform.txt).

Without it this directory is not collected at all (one header line says so), so the
offline job neither runs nor skips these tests; the ``platform`` CI job installs the
extras and runs them.
"""

from __future__ import annotations

import importlib.util

import pytest

_NEEDED = ("fastapi", "httpx", "uvicorn", "jinja2")


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


MISSING = [m for m in _NEEDED if not _importable(m)]
collect_ignore_glob = ["*"] if MISSING else []


def pytest_report_header(config):
    if MISSING:
        return f"qaplatform_http: not collected, missing {', '.join(MISSING)}"
    return None


if not MISSING:
    from gate.integrity import Thresholds
    from qaplatform.cli import bootstrap
    from qaplatform.service import PlatformService
    from qaplatform.store import PlatformStore

    THRESHOLDS = Thresholds(min_executed=1, max_skip_ratio=0.5, min_verdicts=1, min_acquired=0)

    @pytest.fixture
    def db_path(tmp_path, monkeypatch) -> str:
        for name in ("RESULTS_DB", "QAP_DB", "QAP_DISPATCHER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results-dir"))
        return str(tmp_path / "platform.db")

    @pytest.fixture
    def admin_token(db_path) -> str:
        store = PlatformStore.open(db_path)
        raw, _ = bootstrap(store)
        store.close()
        return raw

    @pytest.fixture
    def factory(db_path):
        return lambda: PlatformService(PlatformStore.open(db_path), thresholds=THRESHOLDS)

    @pytest.fixture
    def http(factory, admin_token):
        from fastapi.testclient import TestClient

        from qaplatform.http.app import create_app

        client = TestClient(create_app(factory))
        client.headers["Authorization"] = f"Bearer {admin_token}"
        return client
