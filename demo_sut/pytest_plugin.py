"""Pytest fixtures that run the demo for a test session.

Load with ``-p demo_sut.pytest_plugin`` or by importing the fixtures in a conftest::

    from demo_sut.pytest_plugin import demo_control, demo_http, demo_seed, demo_sut  # noqa: F401

* ``demo_sut`` (session) - starts the demo on a free localhost port in a background thread
  with a fresh SQLite database, yields a :class:`DemoHandle`, stops it at the end. Under
  pytest-xdist each worker has its own session, so its own port and database. When
  ``DEMO_SUT_URL`` is set, no server is started and the handle points at that instance
  (for example one started by CI with ``python -m demo_sut --port 8765``).
* ``demo_control`` (function) - set the clock, faults and settings for one test; at teardown
  everything is put back (real clock, no faults, default settings) and the fault set seen
  during the test is recorded in the test's ``user_properties`` as ``demo_faults``, so a
  report always shows which defects were switched on.
* ``demo_seed`` (function) - a :class:`demo_sut.seeding.DemoSeeder` for customers and
  subscriptions driven to a state over the API.
* ``demo_http`` (function) - an :class:`api.clients.http_engine.HttpEngine` on the demo's
  base URL whose responses feed ``demo_control``'s fault evidence.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from demo_sut.client import DemoClient
from demo_sut.core import DEFAULT_SETTINGS
from demo_sut.seeding import DemoSeeder
from demo_sut.server import DemoServer

ENV_URL = "DEMO_SUT_URL"


@dataclass
class DemoHandle:
    """A running demo: where it is and how to reach it."""

    base_url: str
    client: DemoClient
    db_path: Path | None
    server: DemoServer | None

    @property
    def api_url(self) -> str:
        return self.base_url + "/api/"


class DemoControl:
    """Clock, faults and settings for one test, restored afterwards."""

    def __init__(self, handle: DemoHandle) -> None:
        self.handle = handle
        self.client = handle.client
        self.seen_faults: set[str] = set()
        self.client.observed_faults.clear()

    def freeze(self, instant: str) -> dict[str, Any]:
        return self.client.freeze(instant)

    def shift(self, days: float = 0, seconds: float = 0) -> dict[str, Any]:
        return self.client.shift(days, seconds)

    def faults(self, *names: str) -> list[str]:
        """Make exactly ``names`` active (no names: none)."""
        active = self.client.replace_faults(list(names))
        self.seen_faults.update(active)
        return active

    def fault(self, name: str, on: bool = True) -> list[str]:
        active = self.client.set_faults(**{name: on})
        self.seen_faults.update(active)
        return active

    def settings(self, **changes: Any) -> dict[str, Any]:
        return self.client.settings(**changes)

    def reset_database(self) -> None:
        """Fresh database (seed data only). Clock and faults are reset as well."""
        self.client.reset()

    def observe(self, method: str, url: str, response: Any) -> None:
        """HttpEngine observer: collect the ``X-Demo-Faults`` header of every response."""
        if hasattr(response, "header"):
            value = response.header("X-Demo-Faults")
        else:
            value = getattr(response, "headers", {}).get("X-Demo-Faults")
        if value and value != "none":
            self.seen_faults.update(value.split(","))

    def evidence(self) -> str:
        seen = self.seen_faults | self.client.observed_faults
        seen |= set(self.client.state()["faults"])
        return ",".join(sorted(seen)) or "none"

    def restore(self) -> None:
        self.client.real_time()
        self.client.replace_faults([])
        self.client.settings(**DEFAULT_SETTINGS)


@pytest.fixture(scope="session")
def demo_sut(tmp_path_factory: pytest.TempPathFactory) -> Iterator[DemoHandle]:
    external = os.environ.get(ENV_URL)
    if external:
        url = external.rstrip("/")
        yield DemoHandle(url, DemoClient(url), None, None)
        return
    db_path = tmp_path_factory.mktemp("demo_sut") / "demo.sqlite"
    server = DemoServer(db_path=db_path).start()
    try:
        yield DemoHandle(server.base_url, DemoClient(server.base_url), db_path, server)
    finally:
        server.stop()


@pytest.fixture
def demo_control(demo_sut: DemoHandle, request: pytest.FixtureRequest) -> Iterator[DemoControl]:
    control = DemoControl(demo_sut)
    try:
        yield control
    finally:
        request.node.user_properties.append(("demo_faults", control.evidence()))
        control.restore()


@pytest.fixture
def demo_http(demo_sut: DemoHandle, demo_control: DemoControl) -> Any:
    from api.clients.http_engine import HttpEngine

    engine = HttpEngine(demo_sut.api_url, max_retries=0)
    engine.observe(demo_control.observe)
    return engine


@pytest.fixture
def demo_seed(demo_sut: DemoHandle, demo_control: DemoControl) -> DemoSeeder:
    """A :class:`demo_sut.seeding.DemoSeeder` (implies ``demo_control``, so state is restored)."""
    return DemoSeeder(demo_sut.client)
