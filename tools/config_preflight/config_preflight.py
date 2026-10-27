"""Which stored records would the NEW version of a workflow configuration reject?

A config-driven app keeps records in a state and validates them against that
state's rules. Change the rules and the records already stored do not change
with them: the app rejects them on the next upgrade or the next action. This
tool answers the question before the deploy, from three inputs:

* the NEW config, and optionally the OLD one: workflow YAML in the format
  :mod:`generators.workflow_model` reads, as files or directories, or at git
  refs (read with ``git show``, see :mod:`tools.config_preflight.sources`);
* the records: a JSONL or JSON export, or anything implementing
  :class:`RecordReader`. The paged HTTP lists, the event-history recovery and
  the fetch-by-id live in :mod:`tools.config_preflight.sources`.

Per-state rules. The workflow format declares ``fields`` once for the entity.
A state may add its own ``fields`` list; an entry there is merged over the
entity-level entry of the same name, key by key::

    fields:
      - {name: amount, type: decimal, min: 0}
    states:
      active:
        fields:
          - {name: amount, required: true}   # in `active`: required, decimal, >= 0

For every record the NEW rules of its current state are evaluated. Each rule
ends OK, BLOCKED or UNKNOWN (the :class:`gate.preflight.Status` vocabulary), and
the report lists:

* **rejected** - a BLOCKED rule: record, field, rule and the stored value, and
  whether the OLD config already rejected it;
* **orphaned** - the record's state (or entity type) is gone from the NEW config;
* **stale action** - the record sits in an action (an open draft) its state no
  longer offers;
* **stale route** - the record is headed for a target that is not a declared
  transition target of its state (a state with no routes accepts none);
* **stuck** - the state had a way out in the OLD config and has none in the NEW
  one (no transition to a declared state). Transition conditions are not
  evaluated: a conditional exit counts as an exit;
* **not assessed** - a rule this tool does not understand, a pattern that does
  not compile, a changed reference target, a record without a state. UNKNOWN is
  never counted as valid;
* the config change itself: entity types, states, transitions and field rules
  added, removed, tightened or loosened.

Records valid under the NEW rules with no finding are counted, not listed. A
finding whose every stale name the type's migration script mentions gets the
verdict ``covered-by-migration?``: a hint, still a finding.

Read-only by construction: the tool reads files, runs ``git show``/``git
ls-tree``, issues GETs, and prints to stdout. It writes nothing. The command
line and its exit status are in :mod:`tools.config_preflight.cli`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import yaml

from gate.preflight import Status
from generators.workflow_model import (
    WORKFLOW_SUFFIXES,
    WorkflowDefinition,
    WorkflowError,
    from_mapping,
)

#: Field keys evaluated as rules. ``name`` identifies the field; the descriptive
#: keys carry no rule. Anything else in a field entry is a rule this tool does
#: not understand, and is reported as not assessed.
RULE_KEYS = ("type", "required", "choices", "pattern", "min", "max", "max_len", "ref")
DESCRIPTIVE_KEYS = frozenset({"name", "title", "label", "description", "help", "placeholder"})
KNOWN_TYPES = frozenset(
    {"text", "string", "integer", "number", "decimal", "amount", "boolean", "date", "datetime",
     "choice", "reference"}
)  # fmt: skip


class InputError(Exception):
    """Unreadable config or records, a bad git ref, a bad option: exit 1."""


class Finding(str, Enum):
    REJECTED = "rejected"
    ORPHANED = "orphaned"
    STUCK = "stuck"
    STALE_ACTION = "stale_action"
    STALE_ROUTE = "stale_route"
    NOT_ASSESSED = "not_assessed"


#: The verdict of a finding whose every stale name the type's migration script mentions.
#: A hint for the reader, still a finding: a mention is not a migration.
MIGRATION_HINT = "covered-by-migration?"


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


MISSING: Any = _Missing()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

FieldRules = dict[str, dict[str, Any]]  # field name -> {rule key: value}


@dataclass(frozen=True)
class Entity:
    """One workflow plus the raw field entries the model does not keep per state."""

    definition: WorkflowDefinition
    fields: FieldRules  # entity-level
    state_fields: Mapping[str, FieldRules]  # state -> state-level overrides

    @property
    def key(self) -> str:
        return self.definition.key

    def rules(self, state: str) -> FieldRules:
        merged = {name: dict(entry) for name, entry in self.fields.items()}
        for name, entry in self.state_fields.get(state, {}).items():
            merged.setdefault(name, {}).update(entry)
        return merged

    def exits(self, state: str) -> list[str]:
        """Transitions out of ``state`` that lead to a declared state, as ``action->to``."""
        return [
            f"{t.label}->{t.to}"
            for t in self.definition.states[state].transitions
            if t.to in self.definition.states
        ]


Config = dict[str, Entity]  # entity key -> entity


def _field_entries(raw: Any, where: str) -> FieldRules:
    out: FieldRules = {}
    for entry in raw or ():
        if not isinstance(entry, Mapping) or not entry.get("name"):
            raise InputError(f"{where}: a field entry needs a 'name': {entry!r}")
        out[str(entry["name"])] = {
            str(k): v for k, v in entry.items() if str(k) not in DESCRIPTIVE_KEYS
        }
    return out


def entity_from_mapping(raw: Any, source: str = "") -> Entity:
    try:
        definition = from_mapping(raw, source)
    except WorkflowError as exc:
        raise InputError(str(exc)) from exc
    state_fields = {
        str(name): _field_entries((body or {}).get("fields"), f"{source} state {name}")
        for name, body in raw["states"].items()
        if (body or {}).get("fields")
    }
    return Entity(definition, _field_entries(raw.get("fields"), source), state_fields)


def _parse_yaml(text: str, source: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InputError(f"{source} is not valid YAML: {exc}") from exc


def _add(config: Config, raw: Any, source: str, *, skip_non_workflow: bool) -> None:
    is_workflow = (
        isinstance(raw, Mapping) and raw.get("key") and isinstance(raw.get("states"), Mapping)
    )
    if not is_workflow:
        if skip_non_workflow:
            return
        raise InputError(f"{source}: a workflow needs 'key' and a 'states' mapping")
    entity = entity_from_mapping(raw, source)
    if entity.key in config:
        raise InputError(f"{source}: entity {entity.key!r} is defined twice")
    config[entity.key] = entity


def load_config(path: str | Path) -> Config:
    """A workflow file, or every workflow file under a directory (non-workflows skipped)."""
    path = Path(path)
    config: Config = {}
    if path.is_dir():
        for file in sorted(p for p in path.rglob("*") if p.suffix in WORKFLOW_SUFFIXES):
            _add(config, _parse_yaml(file.read_text(encoding="utf-8"), str(file)), str(file),
                 skip_non_workflow=True)  # fmt: skip
    elif path.is_file():
        _add(config, _parse_yaml(path.read_text(encoding="utf-8"), str(path)), str(path),
             skip_non_workflow=False)  # fmt: skip
    else:
        raise InputError(f"config not found: {path}")
    if not config:
        raise InputError(f"no workflow definition under {path}")
    return config


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise InputError(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        raise InputError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def load_config_at_ref(repo: str | Path, ref: str, path: str) -> Config:
    """``path`` (file or directory, relative to the repo root) as it was at ``ref``."""
    repo = Path(repo)
    names = [
        n
        for n in _git(repo, "ls-tree", "-r", "--name-only", ref, "--", path).splitlines()
        if n.endswith(WORKFLOW_SUFFIXES)
    ]
    if not names:
        raise InputError(f"no workflow file at {ref}:{path}")
    single = len(names) == 1 and names[0] == path.strip("/")
    config: Config = {}
    for name in names:
        source = f"{ref}:{name}"
        raw = _parse_yaml(_git(repo, "show", source), source)
        _add(config, raw, source, skip_non_workflow=not single)
    if not config:
        raise InputError(f"no workflow definition at {ref}:{path}")
    return config


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


class RecordReader(Protocol):
    def records(self) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class FileRecordReader:
    """A ``.jsonl`` export (one object per line) or a ``.json`` list / ``{"records": [...]}``."""

    path: Path

    def records(self) -> Iterator[Mapping[str, Any]]:
        path = Path(self.path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot read records {path}: {exc}") from exc
        if path.suffix in (".jsonl", ".ndjson"):
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise InputError(f"{path}:{number}: not JSON: {exc}") from exc
                yield _record_object(item, f"{path}:{number}")
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(f"{path}: not JSON: {exc}") from exc
        if isinstance(data, Mapping):
            data = data.get("records")
        if not isinstance(data, list):
            raise InputError(f"{path}: expected a list of records or {{'records': [...]}}")
        for index, item in enumerate(data):
            yield _record_object(item, f"{path}[{index}]")


def _record_object(item: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(item, Mapping):
        raise InputError(f"{where}: a record must be an object")
    return item


@dataclass(frozen=True)
class RecordShape:
    """Where a record keeps its id, entity type, state and field values."""

    id_key: str = "id"
    type_key: str = "type"
    state_key: str = "state"
    fields_key: str = "fields"  # a mapping of values; when absent, the record's own keys
    action_key: str = "action"  # the action(s) the record currently sits in (an open draft)
    target_key: str = "target"  # the route target(s) the record is currently headed for

    def values(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        nested = record.get(self.fields_key)
        if isinstance(nested, Mapping):
            return nested
        meta = {self.id_key, self.type_key, self.state_key, self.action_key, self.target_key}
        return {k: v for k, v in record.items() if k not in meta}


# ---------------------------------------------------------------------------
# rule evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleResult:
    field: str
    rule: str
    expected: Any
    value: Any
    status: Status
    detail: str = ""


def _blank(value: Any) -> bool:
    return value is MISSING or value is None or (isinstance(value, str) and not value.strip())


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal | str):
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    return None


def _date(value: Any) -> dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _type_ok(kind: str, value: Any) -> bool:
    if kind in ("text", "string", "choice"):
        return isinstance(value, str)
    if kind == "integer":
        number = _number(value)
        return number is not None and number == number.to_integral_value()
    if kind in ("number", "decimal", "amount"):
        return _number(value) is not None
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "date":
        return _date(value) is not None
    if kind == "datetime":
        if not isinstance(value, str):
            return False
        try:
            dt.datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
    if kind == "reference":
        return isinstance(value, str | int) and not isinstance(value, bool)
    raise AssertionError(kind)


def _comparable(bound: Any, value: Any) -> tuple[Any, Any] | None:
    if isinstance(bound, dt.date) or _date(bound) is not None:
        left = bound if isinstance(bound, dt.date) else _date(bound)
        right = _date(value)
        return (left, right) if right is not None else None
    left, right = _number(bound), _number(value)
    return (left, right) if left is not None and right is not None else None


def check_rule(
    rule: str, expected: Any, value: Any, old_expected: Any = None
) -> Status | tuple[Status, str]:
    """One rule against one stored value: ``Status`` or ``(Status, detail)``.

    A value that is absent passes every rule but ``required``; ``required`` is
    the rule that owns absence. ``old_expected`` is the same rule in the OLD
    config (``MISSING`` when it had none); only ``ref`` looks at it. ``None``
    means "unchanged".
    """
    if old_expected is None:
        old_expected = expected
    if rule == "required":
        if expected is True and _blank(value):
            return Status.BLOCKED, "required, but no value is stored"
        return Status.OK
    if rule == "ref":
        if old_expected != expected and not _blank(value):
            return Status.UNKNOWN, (
                f"reference target changed ({old_expected} -> {expected}); "
                "targets are not resolved offline"
            )
        return Status.OK
    if rule not in RULE_KEYS:
        return Status.UNKNOWN, f"rule {rule!r} is not understood by this preflight"
    if _blank(value):
        return Status.OK
    if rule == "type":
        kind = str(expected)
        if kind not in KNOWN_TYPES:
            return Status.UNKNOWN, f"field type {kind!r} is not understood by this preflight"
        return Status.OK if _type_ok(kind, value) else (Status.BLOCKED, f"not a {kind}")
    if rule == "choices":
        allowed = [str(c) for c in (expected if isinstance(expected, list) else [expected])]
        if str(value) in allowed:
            return Status.OK
        return Status.BLOCKED, f"not one of {allowed}"
    if rule == "pattern":
        try:
            compiled = re.compile(str(expected))
        except re.error as exc:
            return Status.UNKNOWN, f"pattern does not compile: {exc}"
        if compiled.fullmatch(str(value)):
            return Status.OK
        return Status.BLOCKED, f"does not match {expected!s}"
    if rule == "max_len":
        if not isinstance(expected, int) or isinstance(expected, bool):
            return Status.UNKNOWN, f"max_len {expected!r} is not an integer"
        if len(str(value)) <= expected:
            return Status.OK
        return Status.BLOCKED, f"length {len(str(value))} > {expected}"
    # min / max
    pair = _comparable(expected, value)
    if pair is None:
        return Status.UNKNOWN, f"cannot compare {value!r} with bound {expected!r}"
    bound, actual = pair
    if rule == "min":
        return Status.OK if actual >= bound else (Status.BLOCKED, f"below minimum {expected}")
    return Status.OK if actual <= bound else (Status.BLOCKED, f"above maximum {expected}")


def evaluate_values(
    values: Mapping[str, Any], rules: FieldRules, old_rules: FieldRules | None = None
) -> list[RuleResult]:
    out: list[RuleResult] = []
    for name, entry in rules.items():
        value = values.get(name, MISSING)
        old_entry = old_rules.get(name, {}) if old_rules is not None else entry
        for rule, expected in entry.items():
            result = check_rule(rule, expected, value, old_entry.get(rule, MISSING))
            status, detail = result if isinstance(result, tuple) else (result, "")
            out.append(RuleResult(name, rule, expected, value, status, detail))
    return out


# ---------------------------------------------------------------------------
# config diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigChange:
    entity: str
    scope: str  # "entity", "state <name>", "fields", "fields in <state>"
    change: str  # added / removed / tightened / loosened / changed
    subject: str
    old: Any = None
    new: Any = None


def _rule_direction(rule: str, old: Any, new: Any) -> str:
    if rule not in RULE_KEYS or rule == "ref":
        return "added" if old is MISSING else "removed" if new is MISSING else "changed"
    if old is MISSING:
        return "loosened" if rule == "required" and new is not True else "tightened"
    if new is MISSING:
        return "loosened"
    if rule == "required":
        return "tightened" if new is True else "loosened"
    if rule == "choices":
        o = {str(c) for c in (old if isinstance(old, list) else [old])}
        n = {str(c) for c in (new if isinstance(new, list) else [new])}
        return "tightened" if n < o else "loosened" if n > o else "changed"
    if rule in ("min", "max", "max_len"):
        pair = _comparable(old, new)
        if pair is None:
            return "changed"
        before, after = pair
        if rule == "min":
            return "tightened" if after > before else "loosened"
        return "tightened" if after < before else "loosened"
    return "changed"


def _diff_fields(entity: str, scope: str, old: FieldRules, new: FieldRules) -> list[ConfigChange]:
    out: list[ConfigChange] = []
    for name in sorted(old.keys() - new.keys()):
        out.append(ConfigChange(entity, scope, "removed", f"field {name}", old[name], None))
    for name in sorted(new.keys() - old.keys()):
        change = "tightened" if new[name].get("required") is True else "added"
        out.append(ConfigChange(entity, scope, change, f"field {name}", None, new[name]))
    for name in sorted(old.keys() & new.keys()):
        before, after = old[name], new[name]
        for rule in sorted(before.keys() | after.keys()):
            o, n = before.get(rule, MISSING), after.get(rule, MISSING)
            if o == n:
                continue
            change = _rule_direction(rule, o, n)
            if rule not in RULE_KEYS:
                change += " (not understood)"
            out.append(ConfigChange(
                entity, scope, change, f"{name}.{rule}",
                None if o is MISSING else o, None if n is MISSING else n,
            ))  # fmt: skip
    return out


def diff_configs(old: Config, new: Config) -> list[ConfigChange]:
    out: list[ConfigChange] = []
    for key in sorted(old.keys() - new.keys()):
        out.append(ConfigChange(key, "entity", "removed", f"entity type {key}"))
    for key in sorted(new.keys() - old.keys()):
        out.append(ConfigChange(key, "entity", "added", f"entity type {key}"))
    for key in sorted(old.keys() & new.keys()):
        o, n = old[key], new[key]
        o_states, n_states = o.definition.states, n.definition.states
        for state in sorted(o_states.keys() - n_states.keys()):
            out.append(ConfigChange(key, "entity", "removed", f"state {state}"))
        for state in sorted(n_states.keys() - o_states.keys()):
            out.append(ConfigChange(key, "entity", "added", f"state {state}"))
        for state in [s for s in o_states if s in n_states]:
            before = {(t.label, t.to) for t in o_states[state].transitions}
            after = {(t.label, t.to) for t in n_states[state].transitions}
            for label, to in sorted(before - after):
                out.append(ConfigChange(key, f"state {state}", "removed",
                                        f"transition {label} -> {to}"))  # fmt: skip
            for label, to in sorted(after - before):
                out.append(ConfigChange(key, f"state {state}", "added",
                                        f"transition {label} -> {to}"))  # fmt: skip
        out.extend(_diff_fields(key, "fields", o.fields, n.fields))
        for state in sorted(set(o.state_fields) | set(n.state_fields)):
            if state not in n_states:
                continue
            out.extend(_diff_fields(
                key, f"fields in {state}",
                o.state_fields.get(state, {}), n.state_fields.get(state, {}),
            ))  # fmt: skip
    return out


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordFinding:
    kind: Finding
    record: str
    entity: str
    state: str
    field: str = ""
    rule: str = ""
    value: Any = None
    detail: str = ""
    already_under_old: bool = False  # the OLD config rejected it as well
    source: str = "file"  # where the record was read: file, records, drafts, events, ids
    verdict: str = "finding"  # or MIGRATION_HINT

    @property
    def stale_names(self) -> tuple[str, ...]:
        """Config names this finding says are gone; empty when it is about a value."""
        if self.kind is Finding.ORPHANED:
            return (self.entity,) if self.field == "type" else (self.state,)
        if self.kind in (Finding.STALE_ACTION, Finding.STALE_ROUTE):
            return (str(self.value),)
        return ()


def _lookup(config: Mapping[str, Entity], key: str) -> Entity | None:
    """An entity by key; the match is case-insensitive."""
    if key in config:
        return config[key]
    folded = key.casefold()
    return next((e for k, e in config.items() if k.casefold() == folded), None)


def _names(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list | tuple):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]


@dataclass
class PreflightResult:
    old_label: str
    new_label: str
    total: int = 0
    valid: int = 0  # no finding at all under the NEW config
    newly_valid: int = 0  # of those, records the OLD config rejected
    findings: list[RecordFinding] = field(default_factory=list)
    changes: list[ConfigChange] = field(default_factory=list)
    scanned: dict[str, dict[str, int]] = field(default_factory=dict)  # entity -> source -> n
    out_of_scope: int = 0  # records of a type outside --types / the manifest
    undetermined: list[str] = field(default_factory=list)  # what could not be read or judged
    gone: list[dict[str, str]] = field(default_factory=list)  # fetched by id, answered 404
    days: list[dict[str, Any]] = field(default_factory=list)  # event history, one row per day
    compared: bool = True  # False when no OLD config was given

    def of(self, kind: Finding) -> list[RecordFinding]:
        return [f for f in self.findings if f.kind is kind]

    def records_with(self, kind: Finding) -> int:
        return len({(f.entity, f.record) for f in self.of(kind)})

    @property
    def blocking(self) -> bool:
        return any(f.kind is not Finding.NOT_ASSESSED for f in self.findings)

    @property
    def not_assessed(self) -> bool:
        return any(f.kind is Finding.NOT_ASSESSED for f in self.findings)

    @property
    def determined(self) -> bool:
        """Every source was read, every type had a config, and something was scanned."""
        return not self.undetermined and self.total > 0

    def exit_code(self, *, fail_on_findings: bool, allow_not_assessed: bool = False) -> int:
        """2 findings (with ``fail_on_findings``), else 1 undetermined, else 0 clean.

        A finding wins over an incomplete scan: it is already enough to stop a
        deploy, and the report still lists what was not read. Nothing scanned is
        never clean.
        """
        failing = self.blocking or (self.not_assessed and not allow_not_assessed)
        if fail_on_findings and failing:
            return 2
        return 0 if self.determined else 1

    def to_json(self) -> dict[str, Any]:
        def finding(f: RecordFinding) -> dict[str, Any]:
            row = asdict(f)
            row["kind"] = f.kind.value
            row["value"] = None if f.value is MISSING else f.value
            return row

        return {
            "old": self.old_label,
            "new": self.new_label,
            "determined": self.determined,
            "counts": {
                "records": self.total,
                "valid": self.valid,
                "newly_valid": self.newly_valid,
                "out_of_scope": self.out_of_scope,
                **{k.value: self.records_with(k) for k in Finding},
            },
            "scanned": self.scanned,
            "undetermined": self.undetermined,
            "gone": self.gone,
            "days": self.days,
            "changes": [asdict(c) for c in self.changes],
            "findings": [finding(f) for f in self.findings],
        }


class Preflight:
    """Judges records one at a time against the NEW config and collects the result.

    ``old`` may be ``None`` when only the new config is known: there is then no
    config diff, no "already rejected" marking and no stuck detection.
    ``types`` narrows the run to those entity types (case-insensitive); a
    record of another type is counted as out of scope, not judged.
    """

    def __init__(
        self,
        old: Config | None,
        new: Config,
        *,
        shape: RecordShape | None = None,
        old_label: str = "old",
        new_label: str = "new",
        types: Iterable[str] | None = None,
    ) -> None:
        self.old = old or {}
        self.new = new
        self.shape = shape or RecordShape()
        self.types = None if types is None else {t.casefold() for t in types}
        changes = diff_configs(old, new) if old is not None else []
        self.result = PreflightResult(
            old_label, new_label, changes=changes, compared=old is not None
        )

    def in_scope(self, entity: str) -> bool:
        return self.types is None or entity.casefold() in self.types

    def declared(self, entity: str, state: str, action: str | None) -> bool:
        """Does the NEW config declare ``state`` for ``entity``, and ``action`` in it?"""
        found = _lookup(self.new, entity)
        if found is None or state not in found.definition.states:
            return False
        return not action or action in found.definition.states[state].actions

    def check(
        self, record: Mapping[str, Any], source: str = "file", entity: str | None = None
    ) -> list[RecordFinding]:
        """Judge one record; ``entity`` is the type to assume when the record names none."""
        shape = self.shape
        raw_type = record.get(shape.type_key) or entity
        if raw_type and not self.in_scope(str(raw_type)):
            self.result.out_of_scope += 1
            return []
        result = self.result
        result.total += 1
        rid = str(record.get(shape.id_key) or f"#{result.total}")
        if not raw_type and len(self.new) == 1:
            raw_type = next(iter(self.new))
        found = _lookup(self.new, str(raw_type)) if raw_type else None
        label = found.key if found is not None else str(raw_type or "")
        per_type = result.scanned.setdefault(label or "(no type)", {})
        per_type[source] = per_type.get(source, 0) + 1
        findings, old_rejected = self._findings(rid, label, record, source)
        if findings:
            result.findings.extend(findings)
        else:
            result.valid += 1
            result.newly_valid += old_rejected
        return findings

    def _findings(
        self, rid: str, entity_key: str, record: Mapping[str, Any], source: str
    ) -> tuple[list[RecordFinding], bool]:
        """Findings for one record, and whether the OLD config rejected it."""
        shape = self.shape
        state = record.get(shape.state_key)

        def make(kind: Finding, st: str = "", **kw: Any) -> RecordFinding:
            return RecordFinding(kind, rid, entity_key, st, source=source, **kw)

        if not entity_key or not state:
            missing = "entity type" if not entity_key else "state"
            return [make(Finding.NOT_ASSESSED, str(state or ""),
                         detail=f"record has no {missing}")], False  # fmt: skip
        state = str(state)
        values = shape.values(record)
        old_entity = _lookup(self.old, entity_key)
        if old_entity is not None and state not in old_entity.definition.states:
            old_entity = None
        old_rules = old_entity.rules(state) if old_entity is not None else None
        old_rejected = {
            (r.field, r.rule)
            for r in evaluate_values(values, old_rules or {})
            if r.status is Status.BLOCKED
        }

        entity = _lookup(self.new, entity_key)
        if entity is None:
            why = "entity type is not in the new config"
            return [make(Finding.ORPHANED, state, field="type", detail=why)], bool(old_rejected)
        if state not in entity.definition.states:
            why = "state is not in the new config"
            return [make(Finding.ORPHANED, state, detail=why)], bool(old_rejected)

        out: list[RecordFinding] = []
        node = entity.definition.states[state]
        for action in _names(record.get(shape.action_key)):
            if action not in node.actions:
                out.append(make(
                    Finding.STALE_ACTION, state, field=shape.action_key, rule="declared",
                    value=action, detail=f"action is not offered in state {state}",
                ))  # fmt: skip
        targets = {t.to for t in node.transitions}
        for target in _names(record.get(shape.target_key)):
            if target not in targets:
                why = (
                    f"not a declared target of {state}"
                    if targets
                    else f"{state} has no routes, so it accepts none"
                )
                out.append(make(Finding.STALE_ROUTE, state, field=shape.target_key,
                                rule="declared", value=target, detail=why))  # fmt: skip
        for r in evaluate_values(values, entity.rules(state), old_rules):
            if r.status is Status.OK:
                continue
            kind = Finding.REJECTED if r.status is Status.BLOCKED else Finding.NOT_ASSESSED
            out.append(make(
                kind, state, field=r.field, rule=r.rule, value=r.value, detail=r.detail,
                already_under_old=(r.field, r.rule) in old_rejected,
            ))  # fmt: skip
        old_exits = old_entity.exits(state) if old_entity is not None else []
        if old_exits and not entity.exits(state):
            why = f"no way out; removed: {', '.join(old_exits)}"
            out.append(make(Finding.STUCK, state, detail=why))
        return out, bool(old_rejected)


def run(
    old: Config | None,
    new: Config,
    records: Iterable[Mapping[str, Any]],
    *,
    shape: RecordShape | None = None,
    old_label: str = "old",
    new_label: str = "new",
    types: Iterable[str] | None = None,
) -> PreflightResult:
    """Judge an export of records in one call."""
    pre = Preflight(old, new, shape=shape, old_label=old_label, new_label=new_label, types=types)
    for record in records:
        pre.check(record)
    return pre.result


def apply_migration_hints(result: PreflightResult, scripts: Mapping[str, str]) -> int:
    """Mark findings whose every stale name the type's migration script mentions.

    ``scripts`` maps entity type to the script text (case-insensitive key). A
    name matches as a whole word. Returns how many findings were marked.
    """
    folded = {k.casefold(): v for k, v in scripts.items()}
    marked = 0
    for index, f in enumerate(result.findings):
        script = folded.get(f.entity.casefold())
        names = f.stale_names
        if not script or not names:
            continue
        if all(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", script) for n in names):
            result.findings[index] = replace(f, verdict=MIGRATION_HINT)
            marked += 1
    return marked


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _cell(value: Any, limit: int = 60) -> str:
    if value is MISSING:
        text = "(missing)"
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, default=str, sort_keys=True)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_markdown(result: PreflightResult) -> str:
    rejected = result.of(Finding.REJECTED)
    lines = [
        f"# Config preflight: {result.old_label} -> {result.new_label}",
        "",
        f"Records: {result.total} read, {result.valid} valid under the new config, "
        f"{result.records_with(Finding.REJECTED)} rejected, "
        f"{result.records_with(Finding.ORPHANED)} orphaned, "
        f"{result.records_with(Finding.STUCK)} stuck, "
        f"{result.records_with(Finding.STALE_ACTION)} on a stale action, "
        f"{result.records_with(Finding.STALE_ROUTE)} on a stale route, "
        f"{result.records_with(Finding.NOT_ASSESSED)} not assessed.",
    ]
    if result.newly_valid:
        lines.append(f"{result.newly_valid} valid record(s) were rejected by the old config.")
    if result.out_of_scope:
        lines.append(f"{result.out_of_scope} record(s) of a type out of scope were skipped.")
    if not result.determined:
        lines.append(
            "**Undetermined:** nothing was scanned."
            if result.total == 0 and not result.undetermined
            else "**Undetermined:** see below; this report does not cover everything."
        )
    if result.scanned:
        sources = sorted({s for row in result.scanned.values() for s in row})
        lines += ["", "## Scanned", "", f"| Entity | {' | '.join(sources)} |",
                  "|---|" + "---|" * len(sources)]  # fmt: skip
        for entity, row in sorted(result.scanned.items()):
            lines.append(f"| {entity} | {' | '.join(str(row.get(s, 0)) for s in sources)} |")
    if result.undetermined:
        lines += ["", f"## Undetermined ({len(result.undetermined)})", ""]
        lines += [f"- {_cell(u, 200)}" for u in result.undetermined]
    lines += ["", "## Config change", ""]
    if result.changes:
        lines += ["| Entity | Scope | Change | Subject | Old | New |", "|---|---|---|---|---|---|"]
        lines += [
            f"| {c.entity} | {c.scope} | {c.change} | {c.subject} | "
            f"{_cell(c.old) if c.old is not None else ''} | "
            f"{_cell(c.new) if c.new is not None else ''} |"
            for c in result.changes
        ]
    else:
        lines.append("No change." if result.compared else "No old config given; nothing compared.")

    def section(title: str, header: str, rows: list[str]) -> None:
        if rows:
            lines.extend(["", f"## {title} ({len(rows)})", "", header, *rows])

    section(
        "Rejected",
        "| Record | Entity | State | Field | Rule | Value | Why | Old config |\n"
        "|---|---|---|---|---|---|---|---|",
        [
            f"| {f.record} | {f.entity} | {f.state} | {f.field} | {f.rule} | {_cell(f.value)} | "
            f"{_cell(f.detail, 80)} | {'also rejected' if f.already_under_old else 'accepted'} |"
            for f in rejected
        ],
    )
    for kind, title in (
        (Finding.ORPHANED, "Orphaned"),
        (Finding.STALE_ACTION, "Stale action"),
        (Finding.STALE_ROUTE, "Stale route"),
        (Finding.STUCK, "Stuck"),
    ):
        section(
            title,
            "| Record | Entity | State | Name | Why | Source | Verdict |\n"
            "|---|---|---|---|---|---|---|",
            [
                f"| {f.record} | {f.entity} | {f.state} | "
                f"{_cell(f.value) if f.value is not None else ''} | {_cell(f.detail, 100)} | "
                f"{f.source} | {f.verdict} |"
                for f in result.of(kind)
            ],
        )
    grouped: dict[tuple[str, str, str, str, str], list[str]] = {}
    for f in result.of(Finding.NOT_ASSESSED):
        grouped.setdefault((f.entity, f.state, f.field, f.rule, f.detail), []).append(f.record)
    section(
        "Not assessed",
        "| Entity | State | Field | Rule | Why | Records |\n|---|---|---|---|---|---|",
        [
            f"| {e} | {s} | {fld} | {rule} | {_cell(why, 80)} | "
            f"{len(ids)}: {_cell(', '.join(ids), 60)} |"
            for (e, s, fld, rule, why), ids in grouped.items()
        ],
    )
    section(
        "Gone",
        "| Record | Entity | Source |\n|---|---|---|",
        [f"| {g['record']} | {g['entity']} | {g['source']} |" for g in result.gone],
    )
    section(
        "Event history",
        "| Entity | Day (UTC) | Events | Error |\n|---|---|---|---|",
        [
            f"| {d['entity']} | {d['day']} | {d['count']} | {_cell(d['error'] or '', 80)} |"
            for d in result.days
        ],
    )
    return "\n".join(lines) + "\n"
