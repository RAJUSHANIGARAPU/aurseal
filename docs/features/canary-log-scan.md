# Seeded canary log scan

`checks/canary/` finds personal data and secrets that a service wrote into its logs, using
synthetic canaries instead of regex guessing. Every run seeds entities whose name, email and
account fields carry run-unique values. After the run, the services' logs are searched for
those exact values in the run's time window. Only test data carries them, so any hit proves
the service logged the run's personal data. There are no false positives from lookalike
production data, and no pattern to maintain.

## Threat

Services log request bodies, error messages and debug output. When a customer name, an email
address or an account number lands in a log, it is copied to a log store with wider access
and a longer retention than the database it came from. A regex for "looks like an IBAN" both
misses real leaks and flags harmless text. A canary has neither problem: it is a value that
exists nowhere except in this run's test data.

## How canaries work

`CanaryRun` is the hook a test-data builder calls:

```python
from checks.canary import CanaryRun

run = CanaryRun()            # tag = core.run_id (+ worker prefix under sharding)
customer = {
    "name": run.name(),      # canary-<tag>
    "email": run.email(),    # qa+<tag>@example.test
    "iban": run.iban(),      # generated, mod-97 valid, derived from the tag
}
...                          # seed, exercise the flows
run.finish()
run.save("reports/canary/run.json")
```

* `name(i)`, `email(i)` and `iban(i)` are stable for a given tag and index; index 0 is the
  bare value, `i > 0` adds `-<i>` (names, emails) or derives a different account number.
* The IBAN is computed at run time with a fixed synthetic bank code. It passes the checksum, so
  a service that validates accepts it, and no account number is committed to the repository.
* The run artifact (`schema`, `run_id`, `started_at`, `ended_at`, `canaries`) holds the values
  and the window. Under sharding each worker saves its own file; `--run-artifact` is
  repeatable and merges them (same run id required).

## Matching

A canary counts only on a token boundary: the characters on either side must not be a
letter, digit, `_` or `-`. `canary-run-1` is not found in `canary-run-10` or `canary-run-1-x`.
Matching ignores case, and an IBAN printed in groups of four still matches. A hit is
classified as `name`, `email` or `account` per log source. The scanner reports counts only;
a matched log line is never printed, stored, or put in an error message.

## Backends

All three sit behind `LogBackend.search(sources, pattern, start, end, limit=, timeout=)`.
The backend over-fetches with a case-insensitive pattern and reports what it could not see;
the scanner does the boundary matching.

| Backend | Source means | Notes |
|---|---|---|
| `files` | file, directory (recursive) or glob under `--log-root` | always available; a file last modified before the run started is skipped |
| `loki` | a stream selector, default `{job=~".+"}` | `GET /loki/api/v1/query_range` with `<selector> \|~ "<pattern>"`; stdlib `urllib`; bearer token read from the variable named by `--loki-token-env`, never from the command line |
| `cloudwatch` | a log group name | Logs Insights through the `aws` CLI (`start-query`, `get-query-results`); at most 50 groups per query; credentials come from the CLI's own environment or profile |

Limits and failure handling, identical for every backend:

* **Row limit** (`--limit`, default 10000). Reaching it marks the result truncated.
  Truncated is never reported as clean. (A Loki server with a lower
  `max_entries_limit_per_query` rejects the request; that is a backend error, so the target
  is not assessed.)
* **Timeout** (`--timeout`, seconds per target). CloudWatch calls `aws logs stop-query` before
  giving up; Loki's request is aborted; the files scan stops reading.
* **Window**: run start to run end plus `--grace-seconds` (default 120) for late ingestion.
* Errors say a status or an exception class. Response bodies are not echoed.

## Target map and verdicts

```yaml
targets:
  billing:
    sources: [logs/billing]
  orders:
    sources:
      - source: logs/shared.log
        include: "[orders]"        # only lines containing this count for this target
        exclude: "healthcheck"     # lines containing this never count
```

`include` and `exclude` are case-sensitive substrings. They split one log source between
targets that share it.

| Verdict | Meaning |
|---|---|
| `STILL_LEAKING` | at least one canary found in the target's sources, in the window. Proof even when the search was truncated |
| `CLEARED` | every source searched completely, nothing found |
| `NOT_ASSESSED` | a source is missing, the result was truncated, the backend failed or timed out, the target has no source, or the run seeded no canary |

**CLEARED covers only the flows the run exercised.** A code path the run never reached can
still log personal data. Read it as "these flows are clean", not "this service is clean".

## Positive control

```bash
python -m checks.canary selftest
```

The self-test writes a known canary into a temporary log and requires `STILL_LEAKING`. It
also requires `CLEARED` for a log without the canary and for a longer token that only starts
with it, and `NOT_ASSESSED` for a missing source. If one disagrees the scanner is broken.

The same logic applies to a real service. Before anything is fixed, the first scan of a
service that is known or suspected to leak should say `STILL_LEAKING`. If it says `CLEARED`,
suspect the scanner (wrong source, wrong window, logs not shipped yet, a flow the run did
not reach) before believing the service. A scanner that has never been seen to fail proves
nothing.

## Run it

```bash
python -m checks.canary scan --run-artifact reports/canary/run.json \
    --targets canary-targets.yml --backend files --log-root logs/ --out-dir reports/canary
python -m checks.canary scan ... --backend loki --loki-url https://loki.example.test:3100 \
    --loki-token-env LOKI_TOKEN
python -m checks.canary scan ... --backend cloudwatch --aws-region eu-west-1 --aws-profile reporting
```

Output is JSON on stdout (`--format md` for markdown); `--out-dir` writes
`canary-scan.json` and `canary-scan.md`. Exit codes follow `checks._outcome`:

| Exit | Meaning |
|---|---|
| 0 | every target CLEARED |
| 1 | at least one target STILL_LEAKING (also: the self-test found the scanner broken) |
| 2 | incomplete: a target NOT_ASSESSED, or an input could not be read |

## Limits

* It finds exact values only. A service that logs a transformed copy (hashed, truncated,
  reformatted beyond optional IBAN spaces) is not detected.
* Only data that the run seeded is covered, and only flows that the run exercised.
* The scan reads logs that exist: a log store that drops or delays lines turns a leak into
  a miss. The positive control on a real source is the guard.
* Metric rows and a pytest plugin are not part of this change; the verdict maps onto the
  repository's `CheckOutcome` (`fail` / `unknown` / `pass`) for when they are added.
