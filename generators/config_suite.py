"""Turn a workflow definition into pytest cases, and run each case against a system under test.

Case kinds (one :class:`Case` each, wrapped in ``pytest.param`` with a readable id):

====================  =================================================================
``config_key``        the definition's key is not blank
``initial_reachable`` the definition is self-consistent (``problems()`` is empty) and the
                      system under test serves the same machine with the same initial
                      states; a system that does not serve it at all skips with a reason
``declared_actions``  a non-terminal state offers at least one action, and every action it
                      offers is declared under ``actions``
``action_renders``    with the entity in the state, an element ``[data-action="<name>"]``
                      is on the screen. This FAILS when it is missing (it is not a logged
                      warning); a known gap is passed as ``known_missing`` and becomes
                      ``xfail(strict=True)``, so the day it renders the case goes red
``path``              walk one traversal path: drive each transition, and after each one
                      check that an element ``[data-state="<target>"]`` is shown
``state_reachable``   a declared state is reachable from an initial state; a state known
                      to be unreachable carries a ``known_defect`` marker instead of a
                      soft warning
====================  =================================================================

The system under test is reached through a :class:`MachineAdapter` the caller provides,
so the cases stay generic: an adapter knows how to put an entity in a state, run an
action, and read the screen. Every transition and check runs inside an Allure step when
``allure`` is installed, and :func:`attach_graph` attaches the ASCII graph.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import pytest

from generators.state_graph import StateGraph, TraversalPath
from generators.workflow_model import Transition, WorkflowDefinition

KINDS = (
    "config_key",
    "initial_reachable",
    "declared_actions",
    "action_renders",
    "path",
    "state_reachable",
)
UNREACHABLE_SIGNATURE = "is not reachable from any initial state"
#: Case kinds that need a MachineAdapter (the system's screens), not just the definition.
ADAPTER_KINDS = frozenset({"action_renders", "path"})


@dataclass(frozen=True)
class Case:
    kind: str
    definition: WorkflowDefinition
    state: str | None = None
    action: str | None = None
    path: TraversalPath | None = None

    @property
    def id(self) -> str:
        key = self.definition.key
        if self.kind == "path" and self.path is not None:
            return f"{key}:path:{self.path.slug}"
        parts = [key, self.kind]
        parts += [p for p in (self.state, self.action) if p]
        return ":".join(parts)


class MachineAdapter(Protocol):
    """What the cases need from a system under test. Implement only what your cases use."""

    def fetch_machine(self, key: str) -> Mapping[str, Any] | None:
        """The served definition, or ``None`` when the system does not serve this machine."""

    def open_in_state(self, state: str, action: str | None = None) -> None:
        """Put an entity in ``state`` and show its screen (``action``: the one about to be
        checked, for systems where the screen depends on how the state was entered)."""

    def rendered_actions(self, action: str) -> int:
        """How many ``[data-action="<action>"]`` elements the current screen shows."""

    def start(self, path: TraversalPath) -> None:
        """Create an entity in ``path.entry_state`` ready to walk ``path``."""

    def drive(self, source: str, transition: Transition) -> None:
        """Run ``transition`` from ``source`` on the current entity."""

    def shows_state(self, state: str) -> bool:
        """Whether the current entity's screen shows ``[data-state="<state>"]``."""


def _step(title: str) -> contextlib.AbstractContextManager:
    try:
        import allure
    except ImportError:  # pragma: no cover - allure-pytest is a project dependency
        return contextlib.nullcontext()
    return allure.step(title)


def ascii_graph(definition: WorkflowDefinition) -> str:
    return StateGraph(definition).ascii()


def attach_graph(definition: WorkflowDefinition) -> str:
    """Attach the ASCII graph to the current Allure test (when allure is present); return it."""
    text = ascii_graph(definition)
    try:
        import allure

        allure.attach(text, name=f"{definition.key} state graph")
    except ImportError:  # pragma: no cover
        pass
    return text


def _param(case: Case, *marks: Any) -> Any:
    return pytest.param(case, id=case.id, marks=marks)


def with_marks(params: Iterable[Any], kinds: Iterable[str], marks: Iterable[Any]) -> list[Any]:
    """``params`` with ``marks`` added to the cases whose kind is in ``kinds``."""
    kinds, marks = frozenset(kinds), tuple(marks)
    out = []
    for p in params:
        case = p.values[0]
        extra = marks if marks and case.kind in kinds else ()
        out.append(pytest.param(case, id=p.id, marks=(*p.marks, *extra)))
    return out


def generate_cases(
    definition: WorkflowDefinition,
    *,
    known_missing: Mapping[tuple[str, str], str] | None = None,
) -> list[Any]:
    """Config, reachability, declared-action and render cases for one definition.

    Render cases are generated for reachable states only (an unreachable state has no
    screen to open; :func:`unreachable_cases` reports it). ``known_missing`` maps
    ``(state, action)`` to the reason it is known not to render.
    """
    known_missing = dict(known_missing or {})
    graph = StateGraph(definition)
    reachable = graph.reachable()
    cases = [
        _param(Case("config_key", definition)),
        _param(Case("initial_reachable", definition)),
    ]
    for name, state in definition.states.items():
        if not state.is_terminal:
            cases.append(_param(Case("declared_actions", definition, state=name)))
    for name in reachable:
        for action in definition.states[name].actions:
            case = Case("action_renders", definition, state=name, action=action)
            reason = known_missing.pop((name, action), None)
            marks = [pytest.mark.xfail(reason=reason, strict=True, raises=AssertionError)]
            cases.append(_param(case, *marks) if reason else _param(case))
    if known_missing:
        raise ValueError(f"known_missing names unknown or unreachable pairs: {known_missing}")
    return cases


def path_cases(
    definition: WorkflowDefinition,
    *,
    feasible: Callable[[TraversalPath], bool] | None = None,
    cover_only: bool = False,
    max_depth: int | None = None,
) -> list[Any]:
    """One case per traversal path, named by its state chain.

    ``feasible`` drops paths the system cannot walk (for example a confirm that only
    applies to a draft of another kind). ``cover_only`` keeps a smallest-first set of
    paths that still walks every transition the kept paths walk.
    """
    graph = StateGraph(definition)
    found = graph.paths() if max_depth is None else graph.paths(max_depth)
    if feasible is not None:
        found = [p for p in found if feasible(p)]
    if cover_only:
        found = covering(found)
    return [_param(Case("path", definition, path=p)) for p in found]


def covering(paths: Iterable[TraversalPath]) -> list[TraversalPath]:
    """A greedy subset of ``paths`` walking every transition any of them walks."""
    pool = list(paths)

    def edges(p: TraversalPath) -> set[tuple[str, str, str]]:
        return {(p.states[i], s.label, p.states[i + 1]) for i, s in enumerate(p.steps)}

    wanted = set().union(*(edges(p) for p in pool)) if pool else set()
    chosen: list[TraversalPath] = []
    while wanted:
        best = max(pool, key=lambda p: (len(edges(p) & wanted), -len(p.steps)))
        chosen.append(best)
        wanted -= edges(best)
        pool.remove(best)
    return chosen


def unreachable_cases(
    definition: WorkflowDefinition, known_defects: Mapping[str, str] | None = None
) -> list[Any]:
    """One ``state_reachable`` case per declared state.

    ``known_defects`` maps a state known to be unreachable to a defect id; that case
    carries ``known_defect`` (strict expected failure keyed to the reachability
    message), so it is reported as the open defect, and goes red when the state
    becomes reachable or the defect is closed.
    """
    known_defects = dict(known_defects or {})
    cases = []
    for name in definition.states:
        case = Case("state_reachable", definition, state=name)
        defect = known_defects.pop(name, None)
        if defect:
            mark = pytest.mark.known_defect(
                defect,
                reason=f"state {name!r} has no incoming transition",
                raises=AssertionError,
                match=UNREACHABLE_SIGNATURE,
            )
            cases.append(_param(case, mark))
        else:
            cases.append(_param(case))
    if known_defects:
        raise ValueError(f"known_defects names undeclared states: {sorted(known_defects)}")
    return cases


# -- running a case -------------------------------------------------------------------------


def check_config_key(case: Case) -> None:
    assert case.definition.key.strip(), f"{case.definition.source}: the workflow key is blank"


def check_initial_reachable(case: Case, adapter: MachineAdapter | None = None) -> None:
    d = case.definition
    problems = d.problems()
    assert not problems, f"{d.key}: the definition does not hang together: {problems}"
    if adapter is None:
        return
    served = adapter.fetch_machine(d.key)
    if served is None:
        pytest.skip(f"the system under test does not serve machine {d.key!r}")
    assert served.get("key") == d.key, f"served key {served.get('key')!r} != {d.key!r}"
    assert tuple(served.get("initial_states") or ()) == d.initial_states, (
        f"{d.key}: served initial states {served.get('initial_states')} "
        f"!= declared {list(d.initial_states)}"
    )


def check_declared_actions(case: Case) -> None:
    d, state = case.definition, case.definition.states[case.state]
    assert state.actions, f"{d.key}: non-terminal state {state.name!r} offers no action"
    undeclared = [a for a in state.actions if a not in d.actions]
    assert not undeclared, f"{d.key}: state {state.name!r} offers undeclared actions {undeclared}"


def check_action_renders(case: Case, adapter: MachineAdapter) -> None:
    with _step(f"open {case.definition.key} in state {case.state}"):
        adapter.open_in_state(case.state, case.action)
    with _step(f'[data-action="{case.action}"] is rendered'):
        count = adapter.rendered_actions(case.action)
    assert count >= 1, (
        f"{case.definition.key}: action {case.action!r} is not rendered in state {case.state!r} "
        f'(no [data-action="{case.action}"] element)'
    )


def run_path(case: Case, adapter: MachineAdapter) -> None:
    path = case.path
    with _step(f"start in {path.entry_state}"):
        adapter.start(path)
        assert adapter.shows_state(path.entry_state), (
            f'entry state {path.entry_state!r} is not shown ([data-state="{path.entry_state}"])'
        )
    for i, transition in enumerate(path.steps):
        source, target = path.states[i], path.states[i + 1]
        with _step(f"{source} --{transition.label}--> {target}"):
            adapter.drive(source, transition)
            assert adapter.shows_state(target), (
                f"after {source} --{transition.label}--> the screen does not show "
                f'[data-state="{target}"] (path {path.describe()})'
            )


def check_state_reachable(case: Case) -> None:
    graph = StateGraph(case.definition)
    assert case.state in graph.reachable(), (
        f"{case.definition.key}: state {case.state!r} {UNREACHABLE_SIGNATURE} "
        f"{list(case.definition.initial_states)}"
    )


def run_case(case: Case, adapter: MachineAdapter | None = None) -> None:
    """Dispatch ``case`` to its check. Render and path cases need an ``adapter``."""
    if case.kind == "config_key":
        return check_config_key(case)
    if case.kind == "initial_reachable":
        return check_initial_reachable(case, adapter)
    if case.kind == "declared_actions":
        return check_declared_actions(case)
    if case.kind == "state_reachable":
        return check_state_reachable(case)
    if adapter is None:
        raise ValueError(f"case {case.id} needs a MachineAdapter for the system under test")
    if case.kind == "action_renders":
        return check_action_renders(case, adapter)
    if case.kind == "path":
        return run_path(case, adapter)
    raise ValueError(f"unknown case kind {case.kind!r}")
