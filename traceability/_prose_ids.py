"""Collect-only helper plugin: adds the requirement ids each test mentions in prose."""

from __future__ import annotations

import pytest

from traceability.prose import ids_in_name, ids_in_text
from traceability.pytest_plugin import register
from traceability.requirements import Namespace


def pytest_configure(config: pytest.Config) -> None:
    ns = Namespace.from_env()

    def prose(item: pytest.Item) -> dict:
        found: list[str] = []
        name = getattr(item, "originalname", None) or item.name.split("[", 1)[0]
        found += ids_in_name(name, ns)
        found += ids_in_text(getattr(getattr(item, "function", None), "__doc__", None), ns)
        found += ids_in_text(getattr(getattr(item, "cls", None), "__doc__", None), ns)
        callspec = getattr(item, "callspec", None)
        if callspec is not None:
            found += ids_in_text(callspec.id, ns)
        return {"prose_ids": list(dict.fromkeys(found))} if found else {}

    register(config).add_item_attributes(prose)
