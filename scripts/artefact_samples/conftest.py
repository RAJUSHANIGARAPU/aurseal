"""The sample run uses no browser: neutralise the root conftest's browser fixtures."""

import pytest


@pytest.fixture(autouse=True)
def collect_browser_evidence():
    yield


@pytest.fixture(autouse=True)
def enable_artifacts():
    yield


@pytest.fixture(autouse=True)
def gateway_retry():
    yield
