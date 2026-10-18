"""Stored-once rule for a hand-off to a messaging service: before/after diff of its overview.

When one system hands a single event to a messaging service, the service must store exactly
one message for it. A retry after a hand-off that timed out but actually succeeded stores a
second copy, and the partner receives the event twice. :func:`stored_once` compares the
overview of one subject (e.g. one subscription) read just before and just after the hand-off:

* every message seen before is still there under the same id;
* no id appears twice;
* on a successful hand-off exactly one message is new, and it has the expected type, a fresh
  status, the same recipient as the earlier messages and a creation time that does not go
  back before any earlier message;
* on a failed hand-off nothing new appears.

Rows are plain mappings; the keys are configurable.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from typing import Any

Row = Mapping[str, Any]
FRESH_STATUSES = ("NEW", "TO_BE_SENT")


def added(before: Sequence[Row], after: Sequence[Row], *, id_key: str = "id") -> list[Row]:
    """Rows of ``after`` whose id was not in ``before``, in ``after`` order."""
    seen = {row[id_key] for row in before}
    return [row for row in after if row[id_key] not in seen]


def stored_once(
    before: Sequence[Row],
    after: Sequence[Row],
    *,
    handoff_succeeded: bool,
    expected_type: str,
    recipient: str | None = None,
    fresh_statuses: Collection[str] = FRESH_STATUSES,
    id_key: str = "id",
    type_key: str = "type",
    status_key: str = "status",
    recipient_key: str = "recipient",
    created_key: str = "created_at",
) -> list[str]:
    """Every violation of the stored-once rule (empty when it holds)."""
    problems = []
    after_ids = Counter(row[id_key] for row in after)
    duplicated = sorted(str(i) for i, n in after_ids.items() if n > 1)
    if duplicated:
        problems.append(f"duplicate message id(s): {duplicated}")
    lost = [row[id_key] for row in before if row[id_key] not in after_ids]
    if lost:
        problems.append(f"message(s) seen before the hand-off are gone: {lost}")

    new = added(before, after, id_key=id_key)
    if not handoff_succeeded:
        if new:
            problems.append(
                f"the hand-off failed but {len(new)} message(s) were stored: "
                f"{[r[id_key] for r in new]}"
            )
        return problems
    if len(new) != 1:
        described = [(r[id_key], r.get(type_key), r.get(status_key)) for r in new]
        problems.append(f"expected exactly one new message, found {len(new)}: {described}")
        return problems

    (row,) = new
    if row.get(type_key) != expected_type:
        problems.append(f"new message has type {row.get(type_key)!r}, expected {expected_type!r}")
    if row.get(status_key) not in fresh_statuses:
        problems.append(
            f"new message has status {row.get(status_key)!r}, "
            f"expected one of {list(fresh_statuses)}"
        )
    want = recipient if recipient is not None else _common(before, recipient_key)
    if want is not None and row.get(recipient_key) != want:
        problems.append(f"new message goes to {row.get(recipient_key)!r}, expected {want!r}")
    latest = max((str(r.get(created_key) or "") for r in before), default="")
    if latest and str(row.get(created_key) or "") < latest:
        problems.append(
            f"new message created {row.get(created_key)}, before the latest earlier one {latest}"
        )
    return problems


def _common(rows: Sequence[Row], key: str) -> Any:
    values = {row.get(key) for row in rows}
    return values.pop() if len(values) == 1 else None
