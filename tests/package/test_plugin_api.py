"""Entry-point discovery for the three plugin groups."""

from __future__ import annotations

import sys
import types
from importlib.metadata import EntryPoint

import pytest

from aurseal.model.v1 import RunUpload
from aurseal.plugin_api import v1


class _Reporter:
    name = "fake"

    def render(self, upload: RunUpload, out):
        return out


class _NotAReporter:
    name = "nope"


@pytest.fixture
def fake_plugins(monkeypatch):
    module = types.ModuleType("tk_fake_plugin")
    module.Reporter = _Reporter
    module.instance = _Reporter()
    module.Broken = _NotAReporter
    monkeypatch.setitem(sys.modules, "tk_fake_plugin", module)

    def install(*entries):
        eps = [EntryPoint(name, value, v1.REPORTERS) for name, value in entries]
        monkeypatch.setattr(v1, "entry_points", lambda group: [e for e in eps if e.group == group])

    return install


def test_the_groups_are_named_after_the_package():
    assert (v1.IMPORTERS, v1.RESULT_TYPES, v1.REPORTERS) == (
        "aurseal.importers",
        "aurseal.result_types",
        "aurseal.reporters",
    )


def test_discover_loads_classes_and_instances(fake_plugins):
    fake_plugins(("b", "tk_fake_plugin:instance"), ("a", "tk_fake_plugin:Reporter"))
    found = v1.discover(v1.REPORTERS)
    assert list(found) == ["a", "b"]
    assert all(isinstance(p, v1.Reporter) for p in found.values())


def test_a_plugin_without_the_interface_is_named(fake_plugins):
    fake_plugins(("bad", "tk_fake_plugin:Broken"))
    with pytest.raises(v1.PluginError, match="bad does not implement Reporter"):
        v1.discover(v1.REPORTERS)


def test_a_plugin_that_fails_to_import_is_named(fake_plugins):
    fake_plugins(("gone", "tk_missing_module:Thing"))
    with pytest.raises(v1.PluginError, match="gone failed to load"):
        v1.discover(v1.REPORTERS)


def test_an_unknown_group_is_an_error():
    with pytest.raises(v1.PluginError, match="unknown plugin group"):
        v1.discover("aurseal.nothing")
