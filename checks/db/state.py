"""Before/after windows around an operation, judged with ``verification.invariants``.

    with DbState(probe).around({"orders": ("SELECT id, status FROM orders WHERE tag = :tag",
                                           {"tag": run_tag})}) as window:
        place_order()
    window.assert_invariants(InvariantSet(changed("orders"), unchanged("refunds")))

A snapshot is ``{name: rows}``, exactly the before/after shape the invariant
factories evaluate — there is no second assertion language. ``diff()`` says which
rows appeared, disappeared or changed (matched on ``id`` when the rows have one).
With ``record=True`` each snapshot also writes a ``metric_db_snapshot`` row
(row count + digest, never row data).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from checks.db.probe import DbProbe, QuerySet
from verification.invariants import InvariantSet, Violation, assert_invariants, check_invariants


def _freeze(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def digest(rows: list[dict]) -> str:
    """Order-independent digest of a result set."""
    body = "\n".join(sorted(_freeze(r) for r in rows))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RowDiff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[tuple[dict, dict]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def diff_rows(before: list[dict], after: list[dict], key: str = "id") -> RowDiff:
    if before and after and all(key in r for r in [*before, *after]):
        old = {r[key]: r for r in before}
        new = {r[key]: r for r in after}
        return RowDiff(
            added=[new[k] for k in new.keys() - old.keys()],
            removed=[old[k] for k in old.keys() - new.keys()],
            changed=[(old[k], new[k]) for k in old.keys() & new.keys() if old[k] != new[k]],
        )
    old_c, new_c = Counter(map(_freeze, before)), Counter(map(_freeze, after))
    return RowDiff(
        added=[json.loads(r) for r in (new_c - old_c).elements()],
        removed=[json.loads(r) for r in (old_c - new_c).elements()],
    )


class StateWindow:
    def __init__(self, state: DbState, qs: QuerySet, params: Mapping[str, Any] | None) -> None:
        self._state = state
        self._qs = qs
        self._params = dict(params or {})
        self.before = state.take(qs, self._params, phase="before")
        self._after: dict[str, list[dict]] | None = None

    @property
    def after(self) -> dict[str, list[dict]]:
        if self._after is None:
            self._after = self._state.take(self._qs, self._params, phase="after")
        return self._after

    def close(self) -> None:
        _ = self.after

    def check(self, invariants: InvariantSet) -> list[Violation]:
        return check_invariants(self.before, self.after, invariants)

    def assert_invariants(self, invariants: InvariantSet) -> None:
        assert_invariants(self.before, self.after, invariants)

    def diff(self, key: str = "id") -> dict[str, RowDiff]:
        return {name: diff_rows(self.before[name], self.after[name], key) for name in self._qs}


class DbState:
    def __init__(self, probe: DbProbe, *, record: bool = False, sink=None) -> None:
        self.probe = probe
        self.record = record
        self.sink = sink
        self.problems: list[str] = []

    def take(
        self, qs: QuerySet, params: Mapping[str, Any] | None = None, phase: str = "snapshot"
    ) -> dict[str, list[dict]]:
        snap = self.probe.snapshot(qs, params)
        if self.record:
            from checks._sink import write_metrics

            problem = write_metrics(
                "metric_db_snapshot",
                [
                    {"name": name, "phase": phase, "row_count": len(rows), "digest": digest(rows)}
                    for name, rows in snap.items()
                ],
                sink=self.sink,
            )
            if problem:
                self.problems.append(problem)
        return snap

    @contextmanager
    def around(
        self, qs: QuerySet, params: Mapping[str, Any] | None = None
    ) -> Iterator[StateWindow]:
        window = StateWindow(self, qs, params)
        yield window
        window.close()
