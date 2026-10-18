# Platform audit log

Every change made through the platform service leaves one row in an append-only,
hash-chained log. The code is `qaplatform/audit.py`; the table is `qap_audit_log`
(migration `qaplatform/migrations/0002_audit.sql`).

## What it does

- **One row per change, in the same transaction.** Every mutating `PlatformService` method
  runs inside `_write`, which appends the audit row after the change succeeds and before
  the transaction commits. If the change fails, nothing is kept; if the audit insert
  fails, the change is rolled back. A test enumerates the service's mutating methods by
  reflection and asserts each one writes exactly one row, so a new method cannot skip it.
- **What a row says:** sequence number, project, UTC timestamp, actor (user id), token id,
  action (`run.create`, `results.append`, `results.junit`, `verdict.append`,
  `evidence.attach`, `run.finish`, `run_request.create`, and the token, member, project,
  manual and session actions added by later features), target (`run`, `r-...`), a small
  diff, request id and client address.
- **No payloads.** The diff holds ids, counts and changed fields. A batch of 1000 results
  is one row with `{"received": 1000, "added": 998}`; result messages and traces never
  enter the log (they are already in the store), and any long value is cut.
- **Replays are not changes.** An idempotent replay that changes nothing (the same
  `external_id`, a key already answered) writes no row.
- **Hash chain per project:** `row_hash = sha256(prev_hash + canonical JSON of the row)`,
  the first row chained to 64 zeros. `verify` recomputes the chain and names the first
  row that is out of order, whose content was changed, or whose predecessor is missing.
- **Defence in depth:** SQLite triggers refuse `UPDATE` and `DELETE` on the table.

## What it proves, honestly

On its own the chain proves the log is internally consistent: a row edited or deleted by
an ordinary write is caught. Someone with write access to the database file can drop the
triggers and rewrite the *whole* chain consistently; that is caught only if the chain
head was **anchored outside the database** earlier (committed to a repository, posted to a
channel, sent to another system) and compared. `audit head` exists for that anchoring.
It is a log, not compliance: clock integrity, retention policy and access review are
separate concerns.

## How to use

```bash
python -m qaplatform audit verify --project default    # exit 1 when the chain is broken
python -m qaplatform audit head --project default      # {"project", "seq", "head"}: anchor this
python -m qaplatform audit export --project default --out reports/audit/default.jsonl
```

REST (role `lead` or `admin`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/projects/{p}/audit?action=&actor=&since=&cursor=&limit=` | entries, oldest first |
| GET | `/api/v1/projects/{p}/audit/verify` | `{"ok", "rows_checked", "first_bad_seq", "reason", "head"}` |
| GET | `/api/v1/projects/{p}/audit/head` | the current head |

## Configuration

None. Retention: rows never leave the table; `audit export` is the archive path.

## Example

A scheduled job that anchors the head daily and fails when the chain breaks:

```bash
python -m qaplatform audit verify --project default \
  && python -m qaplatform audit head --project default >> audit-anchors.jsonl
```
