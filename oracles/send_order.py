"""Which declared file-ordering does an observed send sequence fit?

A message with several files is sent file by file. Builds of a sender can differ only in the
order they send them (by type; by type then name; by type then file id), and that is visible
in the per-file states and send stamps. A :class:`Hypothesis` declares one ordering;
:func:`violations` checks an observed message against it and quotes every file's values when
it does not fit; :func:`fitting_hypotheses` lists the orderings a message fits, so a message
that fits exactly one of a set of build-specific orderings identifies the build
(:func:`identify`).

Two rules make up "fits":

* **prefix** — the files already sent are the first ones in the declared order (nothing is
  sent while a file ordered before it is still waiting);
* **stamps** — ordered by their send stamp, the sent files follow the declared order.

A message with fewer than two sent files cannot discriminate (:func:`orderable` is False): a
single-file resend is a legitimate exception and must not be read as evidence either way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SENT = "SENT"


@dataclass(frozen=True)
class FileState:
    type: str
    id: str
    name: str
    state: str
    sent_at: str | None

    @property
    def sent(self) -> bool:
        return self.state == SENT and bool(self.sent_at)

    def __str__(self) -> str:
        return f"{self.type}/{self.name}/{self.id}={self.state}@{self.sent_at or '-'}"


@dataclass(frozen=True)
class Hypothesis:
    name: str
    key: Callable[[FileState], tuple[Any, ...]]


BY_TYPE = Hypothesis("type", lambda f: (f.type,))
TYPE_THEN_NAME = Hypothesis("type+name", lambda f: (f.type, f.name))
TYPE_THEN_ID = Hypothesis("type+id", lambda f: (f.type, _id_key(f.id)))


def _id_key(value: str) -> tuple[int, Any]:
    """Numeric ids compare as numbers, anything else (e.g. UUIDs) as text."""
    return (0, int(value)) if value.isdigit() else (1, value)


def files_from(details: Mapping[str, Any], **keys: str) -> list[FileState]:
    """File states from a message-details body (``files`` list); key names are overridable."""
    k = {"type": "type", "id": "file_id", "name": "name", "state": "state", "sent_at": "sent_at"}
    k.update(keys)
    return [
        FileState(
            str(f.get(k["type"], "")),
            str(f.get(k["id"], "")),
            str(f.get(k["name"], "")),
            str(f.get(k["state"], "")),
            f.get(k["sent_at"]),
        )
        for f in details.get("files") or []
    ]


def orderable(files: Sequence[FileState]) -> bool:
    """At least two sent files: anything less cannot tell one ordering from another."""
    return sum(1 for f in files if f.sent) >= 2


def describe(files: Iterable[FileState]) -> str:
    return "; ".join(str(f) for f in files)


def violations(files: Sequence[FileState], hypothesis: Hypothesis) -> list[str]:
    """Why ``files`` do not fit ``hypothesis`` (empty when they do). Quotes every file."""
    declared = sorted(files, key=hypothesis.key)
    problems = []
    waiting = False
    for f in declared:
        if not f.sent:
            waiting = True
        elif waiting:
            problems.append(f"{f.type}/{f.name} was sent while a file before it is still waiting")
            break
    stamped = sorted(
        (f for f in files if f.sent), key=lambda f: (str(f.sent_at), declared.index(f))
    )
    keys = [hypothesis.key(f) for f in stamped]
    for prev, cur, f in zip(keys, keys[1:], stamped[1:], strict=False):
        if cur < prev:
            problems.append(f"{f.type}/{f.name} was sent after a file ordered behind it")
            break
    if problems:
        problems.append(f"files under '{hypothesis.name}': {describe(declared)}")
    return problems


def fitting_hypotheses(files: Sequence[FileState], hypotheses: Iterable[Hypothesis]) -> list[str]:
    """Names of the hypotheses ``files`` fit; empty when the message is not orderable."""
    if not orderable(files):
        return []
    return [h.name for h in hypotheses if not violations(files, h)]


def identify(files: Sequence[FileState], builds: Mapping[str, Hypothesis]) -> str | None:
    """The one build whose ordering the message fits, or None (ambiguous / not orderable)."""
    fits = fitting_hypotheses(files, builds.values())
    matches = [build for build, h in builds.items() if h.name in fits]
    return matches[0] if len(matches) == 1 else None
