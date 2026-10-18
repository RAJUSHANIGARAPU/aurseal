"""An in-memory system under test for the generator cases: it follows the declared machine."""

from __future__ import annotations

from generators.workflow_model import WorkflowDefinition


class FakeMachine:
    """Implements ``generators.config_suite.MachineAdapter`` over a definition.

    ``hidden`` actions are never rendered; ``wrong_target`` maps an action to the state
    it lands in instead of the declared one; ``served`` overrides the served definition.
    """

    def __init__(self, definition: WorkflowDefinition, *, hidden=(), wrong_target=None, served=...):
        self.definition = definition
        self.hidden = set(hidden)
        self.wrong_target = dict(wrong_target or {})
        self.served = served
        self.state: str | None = None
        self.log: list[str] = []

    def fetch_machine(self, key):
        if self.served is not ...:
            return self.served
        d = self.definition
        return {"key": d.key, "initial_states": list(d.initial_states)}

    def open_in_state(self, state, action=None):
        self.state = state
        self.log.append(f"open {state}")

    def rendered_actions(self, action):
        if action in self.hidden:
            return 0
        return int(action in self.definition.states[self.state].actions)

    def start(self, path):
        self.state = path.entry_state
        self.log.append(f"start {self.state}")

    def drive(self, source, transition):
        assert self.state == source
        self.state = self.wrong_target.get(transition.action, transition.to)
        self.log.append(f"{transition.action} -> {self.state}")

    def shows_state(self, state):
        return self.state == state
