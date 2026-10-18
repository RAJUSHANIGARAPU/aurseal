"""Render a workflow definition as a Markdown test spec in the ``specs/`` format.

The spec is the planner's output: a human-readable, version-controlled plan that
``TestGenerator.generate_from_spec`` (and ``generate_from_workflow``) can turn into
code. It holds:

* per state: the actions it offers, their conditions, targets and operations;
* the automatic transitions (taken by the system, no action);
* the flattened field schema with type, required, pattern and other constraints;
* reachability (unreachable states are listed, not hidden) and the ASCII graph;
* suggested ``## Scenario`` sections: create, one per (state, action), required-field
  and pattern validation, and the happy path.

The output is deterministic (no dates, no absolute paths), so a committed spec can be
compared byte for byte with a fresh rendering.
"""

from __future__ import annotations

from pathlib import Path

from generators.state_graph import StateGraph
from generators.workflow_model import FieldSpec, WorkflowDefinition

GENERATED_BY = "python -m generators.cli spec"


def _constraints(f: FieldSpec) -> str:
    out = []
    if f.choices:
        out.append("one of " + ", ".join(f"`{c}`" for c in f.choices))
    if f.pattern:
        out.append(f"pattern `{f.pattern}`")
    if f.ref:
        out.append(f"reference to `{f.ref}`")
    if f.minimum is not None:
        out.append(f"min {f.minimum}")
    if f.maximum is not None:
        out.append(f"max {f.maximum}")
    if f.max_len is not None:
        out.append(f"max length {f.max_len}")
    return "; ".join(out) or "-"


def _scenario(name: str, pre: list[str], steps: list[str], expected: list[str], tags: list[str]):
    lines = [f"## Scenario {name}", "", "### Preconditions"]
    lines += [f"- {p}" for p in pre]
    lines += ["", "### Steps"]
    lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    lines += ["", "### Expected"]
    lines += [f"- {e}" for e in expected]
    lines += ["", "### Tags", " ".join(f"`{t}`" for t in tags), "", "---", ""]
    return lines


def render_spec(definition: WorkflowDefinition, source_name: str = "") -> str:
    d = definition
    graph = StateGraph(d)
    reachable = graph.reachable()
    unreachable = graph.unreachable()
    label = d.title or d.key
    src = source_name or (Path(d.source).name if d.source else d.key)
    lines = [
        "---",
        "seed: false",
        f"feature: {d.key}",
        "---",
        "",
        f"# {label} workflow",
        "",
        f"Generated from `{src}` by `{GENERATED_BY}`. Regenerate it instead of editing it.",
        "",
        "## States and actions",
        "",
    ]
    for name, state in d.states.items():
        flags = []
        if name in d.initial_states:
            flags.append("initial")
        if state.is_terminal:
            flags.append("terminal")
        if name in unreachable:
            flags.append("UNREACHABLE")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"### `{name}` - {state.title or name}{suffix}")
        lines.append("")
        if state.on_entry:
            lines.append(f"On entry: {', '.join(state.on_entry)}")
            lines.append("")
        manual = [t for t in state.transitions if not t.automatic]
        if not manual:
            lines.append("- no actions")
        for t in manual:
            action = d.actions.get(t.action)
            title = d.action_title(t.action)
            text = f"- `{t.action}` ({title}) -> `{t.to}`"
            conditions = [c for c in (t.condition, action.condition if action else None) if c]
            if conditions:
                text += " - when " + " and ".join(conditions)
            if action and action.operations:
                text += f" - operations: {', '.join(action.operations)}"
            lines.append(text)
        lines.append("")

    lines += ["## Automatic transitions", ""]
    automatic = d.automatic_transitions()
    if not automatic:
        lines.append("- none")
    for source, t in automatic:
        cond = f" - when {t.condition}" if t.condition else ""
        lines.append(f"- `{source}` -> `{t.to}`{cond}")
    lines.append("")

    lines += ["## Fields", "", "| Field | Type | Required | Constraints |", "|---|---|---|---|"]
    for f in d.fields:
        lines.append(
            f"| `{f.name}` | {f.type} | {'yes' if f.required else 'no'} | {_constraints(f)} |"
        )
    if not d.fields:
        lines.append("| - | - | - | - |")
    lines.append("")

    lines += ["## Reachability", ""]
    lines.append(f"- reachable from {', '.join(d.initial_states)}: {', '.join(reachable)}")
    lines.append(f"- unreachable: {', '.join(unreachable) if unreachable else 'none'}")
    uncovered = graph.uncovered()
    lines.append(f"- transitions no path walks: {', '.join(uncovered) if uncovered else 'none'}")
    lines += ["", "```text", graph.ascii(), "```", "", "---", ""]

    tags = [d.key]
    required = [f.name for f in d.fields if f.required]
    initial = d.initial_states[0] if d.initial_states else "?"
    lines += _scenario(
        f"create a {d.key}",
        [f"No {d.key} exists for this test"],
        [
            f"Open the create screen for a {d.key}",
            "Fill the required fields: " + (", ".join(f"`{n}`" for n in required) or "none"),
            "Save",
        ],
        [f'The new {d.key} is shown in state `{initial}` ([data-state="{initial}"])'],
        [*tags, "smoke"],
    )
    for name in reachable:
        for t in d.states[name].transitions:
            if t.automatic:
                continue
            pre = [f'A {d.key} in state `{name}` ([data-state="{name}"])']
            action = d.actions.get(t.action)
            for cond in (t.condition, action.condition if action else None):
                if cond:
                    pre.append(f"Condition holds: {cond}")
            lines += _scenario(
                f"{t.action} from {name}",
                pre,
                [f'Click the `[data-action="{t.action}"]` element ({d.action_title(t.action)})'],
                [f'The {d.key} is shown in state `{t.to}` ([data-state="{t.to}"])'],
                [*tags, "regression"],
            )
    checks = [f for f in d.fields if f.required or f.pattern or f.choices]
    if checks:
        steps = []
        expected = []
        for f in checks:
            if f.required:
                steps.append(f"Leave `{f.name}` empty")
                expected.append(f"`{f.name}` is reported as required")
            if f.pattern:
                steps.append(f"Enter a `{f.name}` value that does not match `{f.pattern}`")
                expected.append(f"`{f.name}` is rejected for its format")
            if f.choices:
                steps.append(f"Send a `{f.name}` value outside {', '.join(f.choices)}")
                expected.append(f"`{f.name}` is rejected as not one of the choices")
        steps.append("Save")
        expected.append(f"No {d.key} is created")
        lines += _scenario(
            f"field validation for a {d.key}",
            [f"The create screen for a {d.key} is open"],
            steps,
            expected,
            [*tags, "negative"],
        )
    happy = next((p for p in graph.paths() if p.happy), None)
    if happy is not None:
        lines += _scenario(
            f"happy path {happy.name}",
            [f"A new {d.key} in state `{happy.entry_state}`"],
            [f'Click `[data-action="{a}"]`' for a in happy.actions],
            [f'`[data-state="{s}"]` is shown after each step' for s in happy.states[1:]],
            [*tags, "smoke", "happy_path"],
        )
    while lines and lines[-1] in ("", "---"):
        lines.pop()
    return "\n".join(lines) + "\n"


def write_spec(
    definition: WorkflowDefinition, out_dir: str | Path = "specs", source_name: str = ""
) -> Path:
    """Write ``<out_dir>/<key>.md`` and return its path."""
    out = Path(out_dir) / f"{definition.key}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_spec(definition, source_name), encoding="utf-8")
    return out
