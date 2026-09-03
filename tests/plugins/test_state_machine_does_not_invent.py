"""
A discovered state machine, or none — never a made-up one.

``state_machine_exhaustive`` accepted any class with an attribute named
``state`` as a state machine. For such a class no transition method had been
found, so the generator supplied four names of its own::

    transitions = sm.get("transitions", []) or ["init", "process", "complete", "error"]

and wrote them into a test whose body was ``assert transition`` — an assertion
over a non-empty string constant, which cannot fail. Four green tests per class,
attesting to transitions that exist nowhere in the codebase. The same result
then reported ``transition_counts`` of zero for that class, from the real empty
list, and returned ``pass``.

Two classes whose names lowercased alike both wrote ``test_sm_{name}.py``, so
the second silently overwrote the first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.plugins._tier4 import load, venv_tree

_INVENTED = ["init", "process", "complete", "error"]

MACHINE = '''class Order:
    state = "new"

    def transition_to(self, target):
        self.state = target

    def transition_back(self):
        self.state = "new"
'''

LOOKS_LIKE_ONE = '''class Ledger:
    state = "open"

    def post(self, amount):
        return amount
'''


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """chdir keeps the generated stubs out of the repo's ai_generated_tests/."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    return source


def _run(source):
    plugin = load("state_machine_exhaustive").StateMachineExhaustivePlugin()
    return plugin.run({"source_dir": str(source)})


def _generated(workspace) -> list[Path]:
    out = workspace.parent / "ai_generated_tests" / "state_machine"
    return sorted(out.glob("*.py")) if out.is_dir() else []


class TestItDoesNotInventTransitions:
    def test_a_state_attribute_alone_yields_no_transitions(self, workspace):
        (workspace / "ledger.py").write_text(LOOKS_LIKE_ONE)

        result = _run(workspace)

        assert _generated(workspace) == []
        assert result.findings[0]["state_only_candidates"][0]["class_name"] == "Ledger"

    def test_and_the_run_reaches_no_verdict(self, workspace):
        """It was asked to discover a machine here and could not."""
        (workspace / "ledger.py").write_text(LOOKS_LIKE_ONE)

        assert _run(workspace).status == "unknown"

    def test_the_invented_names_appear_nowhere(self, workspace):
        (workspace / "ledger.py").write_text(LOOKS_LIKE_ONE)
        (workspace / "order.py").write_text(MACHINE)

        _run(workspace)

        written = "\n".join(p.read_text() for p in _generated(workspace))
        assert not any(name in written for name in _INVENTED)


class TestTheGeneratedStubCannotBeMistakenForCoverage:
    def test_it_skips_instead_of_asserting_a_constant(self, workspace):
        (workspace / "order.py").write_text(MACHINE)

        _run(workspace)

        body = _generated(workspace)[0].read_text()
        assert "assert transition" not in body
        assert "pytest.skip" in body


class TestItStillDiscovers:
    """Positive controls: discovering nothing satisfies every test above."""

    def test_real_transition_methods_are_found(self, workspace):
        (workspace / "order.py").write_text(MACHINE)

        result = _run(workspace)

        assert result.findings[0]["state_machines"][0]["transitions"] == [
            "transition_back",
            "transition_to",
        ]

    def test_the_stub_carries_those_names(self, workspace):
        (workspace / "order.py").write_text(MACHINE)

        _run(workspace)

        body = _generated(workspace)[0].read_text()
        assert "transition_to" in body
        assert "transition_back" in body

    def test_the_counts_match_the_transitions_reported(self, workspace):
        """The old result disagreed with itself inside one findings dict."""
        (workspace / "order.py").write_text(MACHINE)

        result = _run(workspace)

        assert result.findings[0]["transition_counts"] == {"order.py::Order": 2}

    def test_a_discovery_run_is_scored_neither_way(self, workspace):
        """It generated stubs and drove nothing, so it has no verdict to give."""
        (workspace / "order.py").write_text(MACHINE)

        assert _run(workspace).status == "skip"


class TestTwoClassesDoNotShareAFile:
    def test_same_name_in_two_modules_writes_two_stubs(self, workspace):
        package = workspace / "billing"
        package.mkdir()
        (workspace / "runner.py").write_text(MACHINE.replace("Order", "Runner"))
        (package / "runner.py").write_text(MACHINE.replace("Order", "Runner"))

        result = _run(workspace)

        assert len(result.findings[0]["state_machines"]) == 2
        assert len(_generated(workspace)) == 2


class TestItDoesNotScanItsOwnDependencies:
    def test_vendored_classes_are_not_discovered(self, workspace):
        (workspace / "order.py").write_text(MACHINE)
        venv_tree(workspace)

        result = _run(workspace)

        discovered = {sm["class_name"] for sm in result.findings[0]["state_machines"]}
        assert discovered == {"Order"}
