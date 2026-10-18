"""State-machine graph analysis over a :class:`~generators.workflow_model.WorkflowDefinition`.

* :meth:`StateGraph.paths` - every path from each initial state, by a cycle-safe
  depth-first walk with a depth cap. A path ends at a terminal state, at the
  depth cap (``truncated``), or on the edge that would revisit a state already on
  the path (``closes_cycle``; the edge is kept so it is covered too). The first
  path found is flagged ``happy``.
* :meth:`StateGraph.unreachable` - states no initial state reaches (breadth-first).
* :meth:`StateGraph.uncovered` - transitions of reachable states no path walks.
* :meth:`StateGraph.ascii` - a plain-text rendering for a report attachment.

Module-level :func:`paths`, :func:`unreachable` and :func:`ascii` take a definition
directly for one-off use.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from generators.workflow_model import Transition, WorkflowDefinition

DEFAULT_MAX_DEPTH = 20


@dataclass(frozen=True)
class TraversalPath:
    states: tuple[str, ...]
    steps: tuple[Transition, ...]
    happy: bool = False
    closes_cycle: bool = False
    truncated: bool = False

    @property
    def name(self) -> str:
        """The state chain: ``draft->pending_payment->active`` (see ``slug`` for a unique id)."""
        return "->".join(self.states)

    @property
    def slug(self) -> str:
        """States and actions, unique per path: ``draft-submit->pending_payment``."""
        out = self.states[0]
        for step, target in zip(self.steps, self.states[1:], strict=True):
            out += f"-{step.label}->{target}"
        return out

    @property
    def entry_state(self) -> str:
        return self.states[0]

    @property
    def terminal_state(self) -> str:
        return self.states[-1]

    @property
    def actions(self) -> tuple[str, ...]:
        return tuple(t.label for t in self.steps)

    @property
    def conditions(self) -> tuple[str, ...]:
        return tuple(t.condition for t in self.steps if t.condition)

    def describe(self) -> str:
        """``draft --submit--> pending_payment --confirm--> active``."""
        out = self.states[0]
        for step, target in zip(self.steps, self.states[1:], strict=True):
            out += f" --{step.label}--> {target}"
        return out


class StateGraph:
    def __init__(self, definition: WorkflowDefinition) -> None:
        self.definition = definition
        self.adjacency: dict[str, list[Transition]] = {
            name: [t for t in state.transitions if t.to in definition.states]
            for name, state in definition.states.items()
        }

    def successors(self, state: str) -> list[str]:
        return [t.to for t in self.adjacency.get(state, [])]

    def reachable(self) -> list[str]:
        """States reachable from any initial state, in breadth-first order."""
        seen: dict[str, None] = {}
        queue = deque(s for s in self.definition.initial_states if s in self.adjacency)
        for s in queue:
            seen.setdefault(s, None)
        while queue:
            current = queue.popleft()
            for nxt in self.successors(current):
                if nxt not in seen:
                    seen[nxt] = None
                    queue.append(nxt)
        return list(seen)

    def unreachable(self) -> list[str]:
        """Declared states no initial state reaches, in declaration order."""
        reached = set(self.reachable())
        return [s for s in self.definition.states if s not in reached]

    def paths(self, max_depth: int = DEFAULT_MAX_DEPTH) -> list[TraversalPath]:
        found: list[TraversalPath] = []

        def emit(states: list[str], steps: list[Transition], **flags: bool) -> None:
            found.append(TraversalPath(tuple(states), tuple(steps), happy=not found, **flags))

        def walk(states: list[str], steps: list[Transition]) -> None:
            current = states[-1]
            outgoing = self.adjacency.get(current, [])
            if not outgoing:
                emit(states, steps)
                return
            if len(steps) >= max_depth:
                emit(states, steps, truncated=True)
                return
            for t in outgoing:
                if t.to in states:
                    emit([*states, t.to], [*steps, t], closes_cycle=True)
                else:
                    walk([*states, t.to], [*steps, t])

        for initial in self.definition.initial_states:
            if initial in self.adjacency:
                walk([initial], [])
        return found

    def uncovered(self, paths: list[TraversalPath] | None = None) -> list[str]:
        """Transitions out of reachable states that no path walks (``state --action--> to``)."""
        paths = self.paths() if paths is None else paths
        walked = {
            (p.states[i], step.label, p.states[i + 1])
            for p in paths
            for i, step in enumerate(p.steps)
        }
        out = []
        for state in self.reachable():
            for t in self.adjacency[state]:
                if (state, t.label, t.to) not in walked:
                    out.append(f"{state} --{t.label}--> {t.to}")
        return out

    def ascii(self) -> str:
        d = self.definition
        width = max((len(s) for s in d.states), default=0) + 2
        lines = [f"{d.key} (initial: {', '.join(d.initial_states) or '-'})"]
        for name, state in d.states.items():
            box = f"[{name}]".ljust(width)
            if not state.transitions:
                lines.append(f"{box} (terminal)")
            for t in state.transitions:
                cond = f"  if {t.condition}" if t.condition else ""
                lines.append(f"{box} --{t.label}--> [{t.to}]{cond}")
                box = " " * width
        missing = self.unreachable()
        lines.append(f"unreachable: {', '.join(missing) if missing else 'none'}")
        return "\n".join(lines)


def paths(definition: WorkflowDefinition, max_depth: int = DEFAULT_MAX_DEPTH):
    return StateGraph(definition).paths(max_depth)


def unreachable(definition: WorkflowDefinition) -> list[str]:
    return StateGraph(definition).unreachable()


def ascii(definition: WorkflowDefinition) -> str:  # noqa: A001 - the plan's public name
    return StateGraph(definition).ascii()
