"""
Comparing names is not comparing behaviour.

``behavioral_equivalence`` snapshotted the public functions of the tree and
diffed them as a flat, repo-wide **set of names**::

    prev_funcs = {f["function"] for f in prev.get("functions", [])}

The file each name came from was collected one line earlier and thrown away, and
each file was capped at five functions. So:

- rewriting every function body left the name set identical and the refactor
  reported no drift whatsoever;
- deleting ``parse()`` from ``a.py`` while ``b.py`` also defined ``parse()``
  produced an empty ``removed``;
- the sixth public function in a file could appear or vanish unseen.

The tests below drive each of those three through the plugin. They are named for
what the plugin can actually establish — a static comparison of the public
surface — because the thing that made the old version convincing was its name.
"""

from __future__ import annotations

import json

import pytest

from tests.plugins._tier4 import StubGovernor, load, venv_tree


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A tree to refactor. chdir keeps the snapshot out of the repo's reports/."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    return source


def _plugin():
    return load("behavioral_equivalence").BehavioralEquivalencePlugin()


def _snapshot(source):
    return _plugin().run({"source_dir": str(source), "trigger": "pre_refactor"})


def _diff(source, governor=None):
    return _plugin().run(
        {
            "source_dir": str(source),
            "trigger": "post_refactor",
            "cost_governor": governor or StubGovernor("semantic"),
        }
    )


class TestABodyChangeIsDrift:
    def test_rewriting_a_body_under_the_same_name_is_seen(self, workspace):
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")
        _snapshot(workspace)
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items) * 2\n")

        result = _diff(workspace)

        assert result.findings[0]["drift"]["body_changed"] == ["mod.py::total"]
        assert result.status == "fail"

    def test_reformatting_a_body_is_not_drift(self, workspace):
        """Control: the hash is over the parsed body, so layout is not a change."""
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")
        _snapshot(workspace)
        (workspace / "mod.py").write_text(
            "def total(items):\n    # a comment\n    return sum(\n        items\n    )\n"
        )

        result = _diff(workspace)

        assert result.findings[0]["drift"]["body_changed"] == []
        assert result.status == "pass"


class TestTheFileIsPartOfTheName:
    def test_a_deletion_masked_by_a_namesake_is_seen(self, workspace):
        """``parse`` leaves a.py, b.py still has one, and ``removed`` was empty."""
        (workspace / "a.py").write_text("def parse(x):\n    return x\n")
        (workspace / "b.py").write_text("def parse(x):\n    return x\n")
        _snapshot(workspace)
        (workspace / "a.py").write_text("def other(x):\n    return x\n")

        result = _diff(workspace)

        assert "a.py::parse" in result.findings[0]["drift"]["removed"]


class TestEveryFunctionIsSnapshotted:
    def test_the_sixth_public_function_is_not_invisible(self, workspace):
        """``visitor.functions[:5]`` capped each file at five."""
        seven = "\n\n".join(f"def f{i}(x):\n    return x" for i in range(7))
        (workspace / "wide.py").write_text(seven + "\n")
        _snapshot(workspace)
        (workspace / "wide.py").write_text(
            "\n\n".join(f"def f{i}(x):\n    return x" for i in range(6)) + "\n"
        )

        result = _diff(workspace)

        assert result.findings[0]["drift"]["removed"] == ["wide.py::f6"]


class TestASignatureChangeNeedsNoModel:
    def test_it_fails_without_asking(self, workspace):
        (workspace / "mod.py").write_text("def send(to, body):\n    return to\n")
        _snapshot(workspace)
        (workspace / "mod.py").write_text("def send(to, body, subject):\n    return to\n")
        governor = StubGovernor("safe")

        result = _diff(workspace, governor)

        assert result.findings[0]["drift"]["signature_changed"] == ["mod.py::send"]
        assert result.status == "fail"
        assert governor.calls == 0, "a broken parameter list is not a question"


class TestItStillReachesVerdicts:
    """Positive controls. A plugin that collected no functions would report no
    drift forever and satisfy nothing worth having."""

    def test_the_snapshot_records_what_it_found(self, workspace):
        (workspace / "mod.py").write_text(
            "class Engine:\n    def start(self):\n        return 1\n\n\n"
            "def helper():\n    return 2\n"
        )

        _snapshot(workspace)

        recorded = json.loads(
            (workspace.parent / "reports" / "behavioral_snapshots" / "snapshot.json").read_text()
        )
        assert {(f["file"], f["qualname"]) for f in recorded["functions"]} == {
            ("mod.py", "Engine.start"),
            ("mod.py", "helper"),
        }

    def test_an_untouched_tree_passes(self, workspace):
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")
        _snapshot(workspace)
        governor = StubGovernor("semantic")

        result = _diff(workspace, governor)

        assert result.status == "pass"
        assert governor.calls == 0, "there was no drift to classify"

    def test_a_safe_classification_clears_the_drift(self, workspace):
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")
        _snapshot(workspace)
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items) * 2\n")

        result = _diff(workspace, StubGovernor("safe"))

        assert result.status == "pass"


class TestItDoesNotSnapshotItsOwnDependencies:
    def test_vendored_functions_stay_out_of_the_snapshot(self, workspace):
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")
        venv_tree(workspace)

        _snapshot(workspace)

        recorded = json.loads(
            (workspace.parent / "reports" / "behavioral_snapshots" / "snapshot.json").read_text()
        )
        assert [f["file"] for f in recorded["functions"]] == ["mod.py"]


class TestAMissingBaselineIsNotAnInapplicableCheck:
    def test_no_snapshot_is_unknown(self, workspace):
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")

        assert _diff(workspace).status == "unknown"

    def test_an_old_format_snapshot_is_unknown(self, workspace):
        """A name-set snapshot cannot be diffed by keys, signatures and hashes;
        reading it anyway reports the whole tree removed and re-added."""
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")
        old = workspace.parent / "reports" / "behavioral_snapshots"
        old.mkdir(parents=True)
        (old / "snapshot.json").write_text(
            json.dumps({"functions": [{"file": "mod.py", "function": "total"}]})
        )

        assert _diff(workspace).status == "unknown"

    def test_taking_a_baseline_is_scored_neither_way(self, workspace):
        """Recording a snapshot is not a verdict; the comparison is."""
        (workspace / "mod.py").write_text("def total(items):\n    return sum(items)\n")

        assert _snapshot(workspace).status == "skip"
