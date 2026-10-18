"""Append-only, hash-chained audit log.

Every mutating :class:`~qaplatform.service.PlatformService` method writes exactly one row
**in the same transaction** as the change it describes, so a change without its audit row
cannot commit and an audit row without its change cannot exist.

Rows are chained per project: ``row_hash = sha256(prev_hash + canonical_json(row))`` with
the first row chained to :data:`GENESIS`. :func:`verify_chain` recomputes every hash and
names the first row that does not match, so an edited, deleted or reordered row is
*detected*, not merely forbidden. The table's triggers refuse UPDATE and DELETE as
defence in depth.

What the chain proves, and what it does not: on its own it proves the log is internally
consistent. Against someone with write access to the database (who could rewrite the whole
chain), it proves something only when the head (:func:`head`) is anchored outside the
database: committed, published or sent elsewhere, then compared later.

Rows carry actor, token, action, target, a small diff (ids, counts, changed fields; never
result payloads, which are already in the store), request id and client address. A result
batch is one row with counts, not one row per test.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qaplatform import context
from results._util import utc_now

GENESIS = "0" * 64
_HASHED = (
    "seq",
    "project_id",
    "ts",
    "actor_user_id",
    "token_id",
    "action",
    "target_type",
    "target_id",
    "diff_json",
    "request_id",
    "source_ip",
    "prev_hash",
)
_DIFF_VALUE_LIMIT = 200


def canonical(row: Mapping[str, Any]) -> str:
    return json.dumps({k: row.get(k) for k in _HASHED}, sort_keys=True, separators=(",", ":"))


def row_hash(row: Mapping[str, Any]) -> str:
    return hashlib.sha256((row["prev_hash"] + canonical(row)).encode("utf-8")).hexdigest()


def _small(value: Any) -> Any:
    """Diff values are ids and counts; anything long is cut so payloads cannot leak in."""
    if isinstance(value, str) and len(value) > _DIFF_VALUE_LIMIT:
        return value[:_DIFF_VALUE_LIMIT] + "..."
    if isinstance(value, (list, tuple)):
        return [_small(v) for v in list(value)[:20]]
    if isinstance(value, dict):
        return {str(k): _small(v) for k, v in list(value.items())[:30]}
    return value


@dataclass(frozen=True)
class ChainReport:
    project: str
    ok: bool
    rows_checked: int
    first_bad_seq: int | None = None
    reason: str = ""
    head: str = GENESIS

    def to_json(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "ok": self.ok,
            "rows_checked": self.rows_checked,
            "first_bad_seq": self.first_bad_seq,
            "reason": self.reason,
            "head": self.head,
        }


def record(
    scoped: Any,
    *,
    actor_user_id: str,
    token_id: str | None,
    action: str,
    target: tuple[str, str],
    diff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one row to ``scoped``'s project chain. Call inside the change's transaction."""
    ctx = context.current()
    head = scoped.audit_head()
    row: dict[str, Any] = {
        "seq": scoped.audit_next_seq(),
        "project_id": scoped.project_id,
        "ts": utc_now(),
        "actor_user_id": actor_user_id,
        "token_id": token_id,
        "action": action,
        "target_type": target[0],
        "target_id": target[1] or "",
        "diff_json": json.dumps(_small(dict(diff or {})), sort_keys=True, default=str),
        "request_id": ctx.request_id,
        "source_ip": ctx.source_ip,
        "prev_hash": head["row_hash"] if head else GENESIS,
    }
    row["row_hash"] = row_hash(row)
    scoped.audit_append(row)
    return row


def verify_chain(scoped: Any) -> ChainReport:
    """Recompute the project's chain; the first row that breaks it is named."""
    prev = GENESIS
    last_seq = 0
    checked = 0
    for row in scoped.audit_chain():
        checked += 1
        if row["seq"] <= last_seq:
            return ChainReport(scoped.project_id, False, checked, row["seq"], "out of order")
        if row["prev_hash"] != prev:
            return ChainReport(
                scoped.project_id,
                False,
                checked,
                row["seq"],
                "prev_hash does not match the previous row (a row was removed or reordered)",
            )
        if row_hash(row) != row["row_hash"]:
            return ChainReport(
                scoped.project_id, False, checked, row["seq"], "row content was changed"
            )
        prev = row["row_hash"]
        last_seq = row["seq"]
    return ChainReport(scoped.project_id, True, checked, head=prev)


def head(scoped: Any) -> dict[str, Any]:
    row = scoped.audit_head()
    return {
        "project": scoped.project_id,
        "seq": row["seq"] if row else 0,
        "head": row["row_hash"] if row else GENESIS,
    }


def export_jsonl(scoped: Any, path: str | Path) -> int:
    """Write the project's chain as JSONL (one row per line, hashes included)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = scoped.audit_chain()
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)


def public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in (*_HASHED, "row_hash")}
    out["diff"] = json.loads(out.pop("diff_json") or "{}")
    out["project"] = out.pop("project_id")
    return out


__all__ = [
    "GENESIS",
    "ChainReport",
    "canonical",
    "export_jsonl",
    "head",
    "public_row",
    "record",
    "row_hash",
    "verify_chain",
]
