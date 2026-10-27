# Release lead time

How long a change takes from merge to production, split into the two waits a
team can act on: **merge -> tag** (waiting for a release to be cut) and
**tag -> prod** (waiting for that release to be deployed).

```bash
python -m tools.release_leadtime --manifest deploy/production.yaml --workspace ~/src \
  --tags 'v*' --deployments deploy/log.csv --since 2026-07-01 -o reports/lead-time.md
```

## What it measures

A change is a first-parent commit on mainline: a merge commit, a squash merge or
a direct commit. For each change in the `--since`/`--until` window:

| Timestamp | Source |
|---|---|
| `merged_at` | commit date of the change on mainline |
| `tagged_at` | date of the first release tag, by tag date, that contains it (tagger date; commit date for a lightweight tag) |
| `deployed_at` | first `--production` deployment, in the log, of a tag that contains it |

The production deployment is taken over every tag that contains the change, not
only the first: when the first tag never reached production, the next deployed
tag is the one that carried the change there.

Per service and overall the report gives the **median** and **p90**
(nearest rank) of merge -> tag, tag -> prod and merge -> prod, and lists the
slowest `--top` changes (by merge -> prod, or merge -> tag where production is
not measured).

## Missing data is never zero

- No deployment log, or no production row for a service: that service's
  production stages are **NOT MEASURED**. The overall production stages pool
  only the services that are measured and the report names them.
- A change in no tag yet, or in tags none of which reached production, is
  **pending**: counted next to the stage, kept out of the percentiles.
- A production deployment dated before its tag was created is a data error: it
  is ignored and the tag is named in the report.
- A service with no clone or no mainline is **Not measured** with the reason.

The report reads no clock: rerunning it over the same refs and inputs gives the
same output.

## Deployment log

CSV with a header, or JSON (a list of objects, or `{"deployments": [...]}`):

```csv
service,tag,environment,deployed_at
svc-billing,v1.4.0,production,2026-07-03T09:12:00Z
,v2.0.1,production,2026-07-04T15:00:00+02:00
```

`service` is optional; a row without one applies to every service with that tag.
Times are ISO 8601; a time without an offset is read as UTC.

## Options

| Option | Default | Effect |
|---|---|---|
| `--service NAME[=DIR]` | | a service and its clone directory under the workspace (repeatable) |
| `--manifest FILE` | | a `promotion_diff` version manifest naming the services; its refs are ignored |
| `--workspace` | `.` | directory holding the clones |
| `--mainline` | `main` | branch changes are merged into; `origin/<name>` preferred |
| `--tags GLOB` | `v*` | release tag glob or name (repeatable) |
| `--deployments FILE` | none | the deployment log |
| `--production` | `production` | environment name of production in the log |
| `--since`, `--until` | open | merge-time window, `--until` exclusive |
| `--top` | `10` | slowest changes to list |
| `--key-pattern` | `\b[A-Z][A-Z0-9]{1,9}-\d+\b` | ticket keys in the JSON output |
| `--json` | markdown | machine-readable output |
| `-o FILE` | stdout | write the report to a file |

Exit status: 0 report written, 1 bad input. The tool does not fetch; fetch the
clones first if they may be stale.

Tests: `tests/tools/test_release_leadtime.py` (offline, fixed commit and tag dates).
