"""Collect-only helper plugin: adds each item's ``known_defect`` id to the trace entry."""

from __future__ import annotations

import pytest

from traceability.pytest_plugin import register


def pytest_configure(config: pytest.Config) -> None:
    def defects(item: pytest.Item) -> dict:
        ids = [str(m.args[0]) for m in item.iter_markers("known_defect") if m.args]
        return {"defects": sorted(set(ids))} if ids else {}

    register(config).add_item_attributes(defects)
