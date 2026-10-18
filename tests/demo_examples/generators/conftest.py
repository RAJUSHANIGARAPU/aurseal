"""Fixtures for the generator examples: the demo service, and the demo as a MachineAdapter."""

from __future__ import annotations

from typing import Any

import pytest

from demo_sut.pytest_plugin import (  # noqa: F401 - fixtures are used by name
    demo_control,
    demo_http,
    demo_seed,
    demo_sut,
)
from tests.demo_examples.generators._demo_machine import DemoMachine


class _LazyPage:
    """Asks pytest for the browser ``page`` only when a case actually touches the screen,
    so the definition-only cases run without a browser."""

    def __init__(self, request: pytest.FixtureRequest) -> None:
        self._request = request
        self._page: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._page is None:
            self._page = self._request.getfixturevalue("page")
        return getattr(self._page, name)


@pytest.fixture
def demo_machine(request, demo_sut, demo_seed, demo_control) -> DemoMachine:  # noqa: F811
    return DemoMachine(_LazyPage(request), demo_sut, demo_seed, demo_control)
