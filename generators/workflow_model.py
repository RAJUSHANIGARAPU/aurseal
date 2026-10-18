"""A neutral workflow (state-machine) definition, read from YAML into frozen dataclasses.

The format is the one ``demo_sut/config/subscription.machine.yaml`` uses::

    key: subscription            # machine key
    title: Subscription
    initial_states: [draft]
    states:
      draft:
        title: Draft
        on_entry: [compute_defaults]          # optional
        transitions:
          - {action: submit, to: pending_payment}
          - {action: activate, to: active, condition: "activate_immediately == true"}
          - {to: archived, automatic: true}   # no action: taken by the system
    actions:
      submit: {title: Submit, condition: "...", operations: [compute_price]}
    fields:
      - {name: start_date, type: date, required: true, pattern: "...", choices: [...]}

Unknown keys are ignored at every level, so a richer file still loads. A
transition without an ``action`` (or with ``automatic: true``) is an automatic
transition. :meth:`WorkflowDefinition.problems` lists what does not hang
together (a transition to an undeclared state, an undeclared action, an initial
state that is not a state); loading never fails on those, so a test can report
them as findings instead of the parser hiding them behind an exception.

:func:`load` reads one file (or an already parsed mapping), :func:`load_dir` every
workflow file under a directory (files without ``key`` and ``states`` are
skipped, the way a config tree also holds files that are not workflows).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_SUFFIXES = (".yaml", ".yml")


class WorkflowError(ValueError):
    """The input is not a workflow definition at all (no key, no states, not a mapping)."""


@dataclass(frozen=True)
class Transition:
    to: str
    action: str | None = None
    condition: str | None = None
    automatic: bool = False

    @property
    def label(self) -> str:
        return self.action or "(automatic)"


@dataclass(frozen=True)
class State:
    name: str
    title: str = ""
    transitions: tuple[Transition, ...] = ()
    on_entry: tuple[str, ...] = ()

    @property
    def actions(self) -> tuple[str, ...]:
        """Action names offered in this state, in declaration order, without repeats."""
        seen: dict[str, None] = {}
        for t in self.transitions:
            if t.action:
                seen.setdefault(t.action, None)
        return tuple(seen)

    @property
    def is_terminal(self) -> bool:
        return not self.transitions


@dataclass(frozen=True)
class Action:
    name: str
    title: str = ""
    condition: str | None = None
    operations: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str = "text"
    required: bool = False
    pattern: str | None = None
    choices: tuple[str, ...] = ()
    ref: str | None = None
    minimum: Any = None
    maximum: Any = None
    max_len: int | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    title: str = ""
    version: Any = None
    initial_states: tuple[str, ...] = ()
    states: Mapping[str, State] = field(default_factory=dict)
    actions: Mapping[str, Action] = field(default_factory=dict)
    fields: tuple[FieldSpec, ...] = ()
    source: str = ""

    def state(self, name: str) -> State:
        return self.states[name]

    def action_title(self, name: str) -> str:
        action = self.actions.get(name)
        return action.title if action and action.title else name

    def transitions(self) -> list[tuple[str, Transition]]:
        """Every ``(from_state, transition)`` pair in declaration order."""
        return [(s.name, t) for s in self.states.values() for t in s.transitions]

    def automatic_transitions(self) -> list[tuple[str, Transition]]:
        return [(s, t) for s, t in self.transitions() if t.automatic]

    def problems(self) -> list[str]:
        """Self-consistency findings; empty when the definition hangs together."""
        out: list[str] = []
        if not self.key.strip():
            out.append("the workflow key is blank")
        if not self.initial_states:
            out.append("no initial state is declared")
        for name in self.initial_states:
            if name not in self.states:
                out.append(f"initial state {name!r} is not a declared state")
        for source, t in self.transitions():
            if t.to not in self.states:
                out.append(f"{source} --{t.label}--> {t.to}: target state is not declared")
            if t.action and t.action not in self.actions:
                out.append(f"{source} --{t.action}-->: action is not declared under 'actions'")
        return out


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _transition(raw: Any) -> Transition:
    if not isinstance(raw, Mapping) or not raw.get("to"):
        raise WorkflowError(f"a transition needs a 'to' state: {raw!r}")
    action = raw.get("action")
    return Transition(
        to=str(raw["to"]),
        action=str(action) if action else None,
        condition=str(raw["condition"]) if raw.get("condition") else None,
        automatic=bool(raw.get("automatic", False)) or not action,
    )


def _field(raw: Any) -> FieldSpec:
    if not isinstance(raw, Mapping) or not raw.get("name"):
        raise WorkflowError(f"a field needs a 'name': {raw!r}")
    return FieldSpec(
        name=str(raw["name"]),
        type=str(raw.get("type", "text")),
        required=bool(raw.get("required", False)),
        pattern=str(raw["pattern"]) if raw.get("pattern") else None,
        choices=_strings(raw.get("choices")),
        ref=str(raw["ref"]) if raw.get("ref") else None,
        minimum=raw.get("min"),
        maximum=raw.get("max"),
        max_len=int(raw["max_len"]) if raw.get("max_len") is not None else None,
    )


def from_mapping(raw: Any, source: str = "") -> WorkflowDefinition:
    """Build a definition from parsed YAML. Raises :class:`WorkflowError` for a non-workflow."""
    if not isinstance(raw, Mapping):
        raise WorkflowError(f"{source or 'input'}: a workflow definition must be a mapping")
    key = raw.get("key")
    states_raw = raw.get("states")
    if not key or not isinstance(states_raw, Mapping):
        raise WorkflowError(f"{source or 'input'}: a workflow needs 'key' and a 'states' mapping")
    states = {
        str(name): State(
            name=str(name),
            title=str((body or {}).get("title", "")),
            transitions=tuple(_transition(t) for t in (body or {}).get("transitions") or ()),
            on_entry=_strings((body or {}).get("on_entry")),
        )
        for name, body in states_raw.items()
    }
    actions = {
        str(name): Action(
            name=str(name),
            title=str((body or {}).get("title", "")),
            condition=str(body["condition"]) if (body or {}).get("condition") else None,
            operations=_strings((body or {}).get("operations")),
        )
        for name, body in (raw.get("actions") or {}).items()
    }
    return WorkflowDefinition(
        key=str(key),
        title=str(raw.get("title", "")),
        version=raw.get("version"),
        initial_states=_strings(raw.get("initial_states")),
        states=states,
        actions=actions,
        fields=tuple(_field(f) for f in raw.get("fields") or ()),
        source=source,
    )


def load(source: str | Path | Mapping[str, Any]) -> WorkflowDefinition:
    """A definition from a YAML file path or an already parsed mapping."""
    if isinstance(source, Mapping):
        return from_mapping(source)
    path = Path(source)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkflowError(f"cannot read workflow file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise WorkflowError(f"{path} is not valid YAML: {exc}") from exc
    return from_mapping(raw, str(path))


def load_dir(directory: str | Path) -> list[WorkflowDefinition]:
    """Every workflow definition under ``directory`` (recursive), sorted by path.

    Files that parse but are not workflows (no ``key``/``states``) are skipped;
    a file that is not valid YAML still raises, because a broken config is a finding.
    """
    found = []
    for path in sorted(Path(directory).rglob("*")):
        if path.suffix not in WORKFLOW_SUFFIXES or not path.is_file():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise WorkflowError(f"{path} is not valid YAML: {exc}") from exc
        try:
            found.append(from_mapping(raw, str(path)))
        except WorkflowError:
            continue
    return found
