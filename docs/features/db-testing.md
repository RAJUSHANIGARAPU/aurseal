# Database testing helpers

## What it does

`checks.db` lets a test look at a database around an operation, read-only, and judge
the change with the same invariants used for API state.

- **Probes** (`open_probe(url)`):
  - `SqliteProbe` (stdlib): opens the file with `mode=ro`, so the engine itself refuses
    writes, and runs the statement guard first;
  - `SqlAlchemyProbe` (optional SQLAlchemy Core, imported lazily): statement guard,
    every query inside a transaction that is always rolled back, and a read-only
    transaction on PostgreSQL/MySQL.
- **Statement guard** (`read_only_problem`, `require_read_only`): one `SELECT` or
  `WITH ... SELECT` only. Refuses more than one statement, any write or schema keyword
  anywhere (a CTE can wrap an `UPDATE`), `SELECT ... INTO`, and row locks. Comments
  and string literals are blanked first, so they can neither hide nor fake a keyword.
- **Snapshots**: a query set names queries (`{"orders": "SELECT id, status FROM orders
  WHERE tag = :tag"}`); `snapshot()` returns `{name: rows}`, the exact before/after
  shape `verification.invariants` evaluates. Parameters are bound, never interpolated.
- **State windows** (`DbState(probe).around(query_set, params)`): snapshot before,
  snapshot after (taken when the block exits), then `assert_invariants(...)` lists every
  violated rule and `diff()` names the rows added, removed or changed (matched on `id`).
- **Read helpers** (`checks.db.read_paths`): `count_rows`, `rows_where`,
  `tagged_query_set` (one query per table, scoped to the run tag). A source test keeps
  any writer call out of this module.
- **Writes** for fixture setup only: `probe.writer(allow_writes=True)` is refused unless
  `DB_WRITES_ALLOWED=true`; a write given an `undo` statement is recorded in the cleanup
  ledger.
- **Fixtures** (`-p checks.db.pytest_plugin`): `db_probe`, `db_state`. Without a
  database configured the test is skipped with a named reason, never passed.

## How to use

```python
from checks.db import DbState, open_probe
from checks.db.read_paths import tagged_query_set
from verification.invariants import InvariantSet, changed, unchanged

probe = open_probe("sqlite:///reports/app.sqlite")
queries = tagged_query_set({"orders": ["id", "status"], "refunds": ["id", "amount"]}, "run_tag")
with DbState(probe).around(queries, {"tag": run_tag}) as window:
    cancel_order(order_id)
window.assert_invariants(InvariantSet(changed("orders"), changed("refunds")))
```

```bash
DB_URL=sqlite:///reports/app.sqlite pytest -p checks.db.pytest_plugin tests/db
python -m checks.db snapshot --url sqlite:///reports/app.sqlite --queries q.json --params '{"tag": "r1"}'
pip install -r requirements-checks.txt   # only for non-SQLite databases
```

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `DB_URL` / `--db-url` | none (tests skip) | `sqlite:///path` or any SQLAlchemy URL |
| `DB_WRITES_ALLOWED` | `false` | Must be `true` for `writer(allow_writes=True)` |
| `DB_SNAPSHOT_METRICS` | `false` | Fixture records a `metric_db_snapshot` row per snapshot |
| `METRICS_SINK` / `METRICS_DIR` | `jsonl` / `reports/metrics` | Where snapshot metrics go |

Snapshot metrics hold the row count and an order-independent digest, never row data.

## Example

```text
E   AssertionError: 2 invariant(s) violated:
E     - orders changed: still [{'id': 1, 'status': 'open'}]
E     - refunds unchanged: changed from [] to [{'id': 7, 'amount': 5.0}]
```

## Honest limits

- The statement guard is defence in depth, not a security boundary: without a
  read-only database user a determined statement can still write on some engines.
  On a shared database, use a read-only user.
- Snapshots of large tables are expensive; scope query sets by the run tag.
- There is no REST endpoint for database access, on purpose: frameworks run their own
  database checks and push results like any other test result.
