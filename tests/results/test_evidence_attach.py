"""attach() on a worker reaches the controller's evidence rows."""

from __future__ import annotations

import pytest

from results.evidence import EVIDENCE_PROPERTY, attach, describe
from tests.results._helpers import rows
from tests.results.conftest import PLUGIN

pytest_plugins = ["pytester"]


def test_attach_under_xdist_lands_in_the_store(pytester, db_path, subprocess_env, monkeypatch):
    monkeypatch.setenv("RESULTS_ARTIFACT_ROOT", str(pytester.path))
    pytester.makepyfile(
        """
        import pathlib
        from results.evidence import attach

        def test_a(request, tmp_path):
            shot = pathlib.Path("shots") / "a.png"
            shot.parent.mkdir(exist_ok=True)
            shot.write_bytes(b"png")
            attach(request, "screenshot", shot)
            attach(request, "trace", "https://ci.example.test/trace.zip")
            assert False
        """
    )
    pytester.runpytest_subprocess(*PLUGIN, "-n", "2")
    ev = {r["kind"]: r for r in rows(db_path, "SELECT * FROM evidence")}
    assert ev["screenshot"]["uri"] == "shots/a.png" and ev["screenshot"]["bytes"] == 3
    assert len(ev["screenshot"]["sha256"]) == 64 and ev["screenshot"]["result_id"]
    assert ev["trace"]["uri"] == "https://ci.example.test/trace.zip"


def test_describe_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        describe("hologram", "x")


def test_attach_needs_a_node():
    with pytest.raises(TypeError):
        attach(object(), "log", "x.log")


class _Node:
    def __init__(self):
        self.user_properties = []


def test_attach_appends_a_user_property(tmp_path, monkeypatch):
    monkeypatch.setenv("RESULTS_ARTIFACT_ROOT", str(tmp_path))
    node = _Node()
    attach(node, "log", tmp_path / "missing.log")
    key, value = node.user_properties[0]
    assert key == EVIDENCE_PROPERTY and value["uri"] == "missing.log" and value["sha256"] is None
