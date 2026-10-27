"""Ownership guard: may this run delete an item the system produced for it?

The cleanup ledger only touches what a test created through its own calls.
Some leftovers are produced by the system on the test's behalf (a generated
message, a scheduled follow-up, a derived document) and are found later by a
lookup. Deleting one of those is irreversible, and a lookup that matched the
wrong record deletes somebody else's data.

:func:`refusal` is the pure check, run on a *fresh* read right before the
delete. It returns ``None`` when every condition holds, else the reason:

* **exact key** - only rows whose key equals ``owner_key`` count; a ``LIKE`` or
  prefix filter on the server is not trusted;
* **only one** - exactly one row carries the key, and it is ``item_id``;
* **created by this run** - at or after ``run_started - skew`` (the server's
  clock and the runner's differ by up to ``skew``);
* **untouched** - still in one of ``initial_states``; an item that moved on
  is in use by something.

:meth:`test_data.cleanup.CleanupLedger.record_owned` wires it into teardown.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from oracles.time_window import TimestampError, parse_instant

Row = Mapping[str, Any]


def _instant(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TimestampError(f"creation time {value!r} has no offset")
        return value
    return parse_instant(value)


def refusal(
    item_id: str,
    owner_key: str,
    rows: Iterable[Row],
    *,
    run_started: datetime,
    skew: timedelta,
    key_of: Callable[[Row], Any],
    created_of: Callable[[Row], Any],
    state_of: Callable[[Row], Any],
    initial_states: Collection[str],
    id_of: Callable[[Row], Any] = lambda r: r.get("id"),
) -> str | None:
    """``None`` when ``item_id`` is provably this run's untouched item, else why not."""
    if not owner_key:
        return "no owner key: refusing to match on an empty key"
    if run_started.tzinfo is None or run_started.utcoffset() is None:
        raise TimestampError(f"run start {run_started!r} is naive; pass an aware datetime")
    matches = [r for r in rows if str(key_of(r)) == owner_key]
    if not matches:
        return f"no item carries key {owner_key!r} exactly"
    if len(matches) > 1:
        ids = [str(id_of(r)) for r in matches]
        return f"{len(matches)} items carry key {owner_key!r} (ids {ids}); none is provably ours"
    row = matches[0]
    if str(id_of(row)) != str(item_id):
        return f"the item for key {owner_key!r} is {id_of(row)}, not {item_id}"
    try:
        created = _instant(created_of(row))
    except TimestampError as exc:
        return f"{item_id}: creation time unreadable ({exc})"
    earliest = run_started - skew
    if created < earliest:
        return f"{item_id} was created at {created.isoformat()}, before this run ({earliest})"
    state = state_of(row)
    if state not in initial_states:
        return f"{item_id} is {state!r}, no longer in {sorted(initial_states)}"
    return None
