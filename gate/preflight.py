"""Read-only preflight: may a test write to this existing entity, and is it the right one?

Picking an existing record out of a shared environment and writing to it is
how a suite corrupts data other people depend on — or drives a broken record
into a state the server cannot get out of. Two different questions get asked
before a write, and they have different consequences:

* **Safety** — would writing to it be harmful or meaningless? A dangling or
  cancelled reference, an open draft someone else holds, an expired or
  terminated record. An unsafe candidate is *set aside for the whole run*:
  no test may touch it.
* **Suitability** — is it the shape this test needs? Fully paid, on a
  particular plan. An unsuitable candidate is merely *left for
  other tests*.

A check that cannot decide (it raised, or the data was not there) is UNKNOWN.
UNKNOWN safety is treated as unsafe: not knowing is not permission.

Checks are pure functions over a snapshot; :func:`run_preflight` fetches the
snapshot through a :class:`~gate.read_only.ReadOnlyClient`, so a preflight
cannot write even by mistake.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gate.read_only import ReadOnlyClient


class Kind(str, Enum):
    SAFETY = "safety"
    SUITABILITY = "suitability"


class Status(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Disposition(str, Enum):
    USE = "use"
    SET_ASIDE = "set_aside"  # unsafe: nobody touches it this run
    NOT_SUITABLE = "not_suitable"  # safe, but not what this test needs


@dataclass(frozen=True)
class Check:
    name: str
    kind: Kind
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class CheckSpec:
    """A named predicate over the snapshot: True = OK, False = BLOCKED, None = UNKNOWN.

    ``fn`` may also return ``(bool | None, detail)``.
    """

    name: str
    kind: Kind
    fn: Callable[[Mapping[str, Any]], Any]
    need: str | None = None  # suitability checks run only when the test declares this need

    def run(self, snapshot: Mapping[str, Any]) -> Check:
        try:
            value = self.fn(snapshot)
        except Exception as exc:
            return Check(self.name, self.kind, Status.UNKNOWN, f"check raised: {exc!r}")
        detail = ""
        if isinstance(value, tuple):
            value, detail = value
        if value is None:
            return Check(self.name, self.kind, Status.UNKNOWN, detail or "not observed")
        return Check(self.name, self.kind, Status.OK if value else Status.BLOCKED, detail)


@dataclass(frozen=True)
class PreflightReport:
    subject: str
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def safe(self) -> bool:
        return all(c.status is Status.OK for c in self.checks if c.kind is Kind.SAFETY)

    @property
    def suitable(self) -> bool:
        return all(c.status is Status.OK for c in self.checks if c.kind is Kind.SUITABILITY)

    @property
    def disposition(self) -> Disposition:
        if not self.safe:
            return Disposition.SET_ASIDE
        return Disposition.USE if self.suitable else Disposition.NOT_SUITABLE

    def reasons(self) -> list[str]:
        return [
            f"{c.kind.value}:{c.name} {c.status.value}{' - ' + c.detail if c.detail else ''}"
            for c in self.checks
            if c.status is not Status.OK
        ]


def evaluate(
    subject: str,
    snapshot: Mapping[str, Any],
    specs: Sequence[CheckSpec],
    needs: Iterable[str] = (),
) -> PreflightReport:
    """Run every safety check, and the suitability checks for the declared ``needs``."""
    needs = frozenset(needs)
    selected = [s for s in specs if s.kind is Kind.SAFETY or s.need is None or s.need in needs]
    return PreflightReport(subject, tuple(spec.run(snapshot) for spec in selected))


def run_preflight(
    client: ReadOnlyClient,
    subject: str,
    reads: Mapping[str, str],
    specs: Sequence[CheckSpec],
    needs: Iterable[str] = (),
    *,
    decode: Callable[[Any], Any] = lambda response: response.json(),
) -> PreflightReport:
    """Fetch ``reads`` ({snapshot key: path}) read-only, then evaluate.

    A read that fails leaves its key absent, so the checks that needed it
    report UNKNOWN rather than the preflight raising.
    """
    snapshot: dict[str, Any] = {}
    for key, path in reads.items():
        try:
            snapshot[key] = decode(client.get(path))
        except Exception:
            continue
    return evaluate(subject, snapshot, specs, needs)
