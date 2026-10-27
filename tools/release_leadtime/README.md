# Release lead time

How long work takes to reach production, split into the waits a team can act
on: waiting for a release to be cut, and waiting for that release to move from
environment to environment.

```bash
python -m tools.release_leadtime --manifest deploy/production.yaml --workspace ~/src \
  --deploy-repo ~/src/deploy --deploy-file 'envs/{env}/versions.yaml' \
  --envs test,acceptance,production --production production,production-dr \
  --since 2026-07-01 --until 2026-10-01 -o reports/lead-time.md
```

## Two views

**Changes.** The unit is a change: a first-parent commit on mainline (a merge
commit, a squash merge or a direct commit), windowed on its merge time.

| Stage | From | To |
|---|---|---|
| merge -> tag | commit date on mainline | date of the first annotated release tag containing it |
| merge -> prod | commit date on mainline | first production arrival of any tag containing it (when the first tag never reached production, the next deployed one did) |

**Releases.** The unit is one `(service, tag)`, counted once, windowed on its
production time. A version reaches an environment at its FIRST deployment
there; a later re-pin (a rollback, a moved URL) is ignored. Production is the
earliest of the `--production` environments. Stages run between consecutive
environments of `--envs`, plus tag -> production. A stage counts only when both
timestamps exist and are in order, so a hotfix that skipped acceptance drops out
of the acceptance stages only. Rows are split into **hotfix** and **regular** by
`--hotfix-pattern`.

Every row gives n, the median, the p90 (nearest rank) and how many units fall
at or under each `--threshold` (default 6 h and 24 h). **Medians are not
additive**: the median of merge -> prod is not the sum of its stage medians.

## Tag dates

An annotated tag is dated by its tagger date. A lightweight tag has no date of
its own (only its commit's), so it is listed apart and counted only in the
environment stages: never in merge -> tag or tag -> production.

## Missing data is never zero

- No deployment data, or none for a service: the stages that need it are
  **NOT MEASURED**. Overall rows pool only the services that are measured, and
  the report names them.
- A change or release that has not reached the end of a stage yet is
  **pending**: counted next to n, kept out of the percentiles.
- A production arrival dated before its annotated tag is a data error: that tag
  is left out of the production stages and named in the report. An unreadable
  revision of a deploy manifest is skipped and named.
- A service with no clone or no mainline is **not measured**, with the reason.

The report reads no clock: rerunning it over the same refs and inputs gives the
same output.

## Deployment data

Either or both:

**A log**, CSV with a header or JSON (a list, or `{"deployments": [...]}`):

```csv
service,tag,environment,deployed_at
svc-billing,v1.4.0,production,2026-07-03T09:12:00Z
,v2.0.1,acceptance,2026-07-04T15:00:00+02:00
```

`service` is optional; a row without one applies to every service with that tag.
Times are ISO 8601; a time without an offset is read as UTC.

**A git repository of deploy manifests** (`--deploy-repo`), one manifest per
environment at `--deploy-file` (`{env}` is replaced), in the `tools/promotion_diff`
format. The committer date of the commit that first pins a tag for an
environment is the time it got there.

## Options

| Option | Default | Effect |
|---|---|---|
| `--service NAME[=DIR]` | | a service and its clone under the workspace (repeatable) |
| `--manifest FILE` | | a version manifest naming the services; its refs are ignored |
| `--workspace` | `.` | directory holding the clones |
| `--mainline` | `main` | branch changes are merged into; `origin/<name>` preferred |
| `--tags GLOB` | `v*` | release tag glob or name (repeatable) |
| `--deployments FILE` | none | the deployment log |
| `--deploy-repo DIR` | none | repository of deploy manifests |
| `--deploy-file` | `{env}.yaml` | manifest path per environment in that repository |
| `--deploy-ref` | `HEAD` | which history of that repository to read |
| `--envs` | `production` | ordered environments, comma-separated |
| `--production` | `production` | production environments, comma-separated; the earliest counts |
| `--hotfix-pattern` | `-hotfix-\d+$` | regex marking a hotfix tag |
| `--threshold H` | `6`, `24` | hours; count units at or under it (repeatable) |
| `--since`, `--until` | open | the window, `--until` exclusive (UTC when no offset) |
| `--top` | `10` | slowest changes to list |
| `--key-pattern` | `\b[A-Z][A-Z0-9]{1,9}-\d+\b` | ticket keys in the JSON output |
| `--json` | markdown | machine-readable output, every release unit included |
| `-o FILE` | stdout | write the report to a file |

Exit status: 0 report written, 1 bad input. The tool does not fetch; fetch the
clones first if they may be stale.

Tests: `tests/tools/test_release_leadtime.py` (offline, fixed commit and tag dates).
