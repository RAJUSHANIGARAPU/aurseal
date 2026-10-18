"""Fixtures for the platform tests: a private store per test and an admin token for it."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from qaplatform.cli import bootstrap
from qaplatform.service import PlatformService
from qaplatform.store import PlatformStore
from tests.qaplatform._helpers import THRESHOLDS, Api

_ENV = (
    "RESULTS_DB",
    "RESULTS_DIR",
    "QAP_DB",
    "QAP_URL",
    "QAP_TOKEN",
    "QAP_PROJECT",
    "QAP_DISPATCHER",
    "QAP_RELEASE_POLICY",
    "QAP_RELEASE_WAIVERS",
    "RUN_TRUST_MIN_EXECUTED",
    "RUN_TRUST_MAX_SKIP_RATIO",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results-dir"))


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "platform.db")


@pytest.fixture
def store(db_path) -> Iterator[PlatformStore]:
    st = PlatformStore.open(db_path)
    yield st
    st.close()


@pytest.fixture
def admin_token(store) -> str:
    raw, _ = bootstrap(store)
    return raw


@pytest.fixture
def service(store) -> PlatformService:
    return PlatformService(store, thresholds=THRESHOLDS)


@pytest.fixture
def admin(service, admin_token):
    return service.authenticate(admin_token)


@pytest.fixture
def api(service, admin_token) -> Api:
    return Api(service, admin_token)
