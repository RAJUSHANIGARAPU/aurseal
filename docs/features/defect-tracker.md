# Defect tracker

Defect state flows back from the tracker into the detectors, and failures flow out as
issue drafts. GitHub Issues is the tracker; a committed YAML file is the offline default
and the test double. **Auto-filing is off by default**; nothing is filed unless asked.
The code lives in `integrations/trackers/` (adapters) and `traceability/defects.py`.

## Trackers and the prefix map

**What it does.** `integrations/trackers.yaml` maps id prefixes to trackers, and is the
single authority on what a defect id is (a requirement prefix such as `REQ` is refused):

```yaml
trackers:
  file:   {type: file, path: defects/defects.yaml, prefixes: [BUG]}
  github: {type: github, repo: "${GITHUB_REPOSITORY}", prefixes: [GH], labels: [bug],
           severity_label_prefix: "severity:"}
```

`GH-57` is issue #57 of the repository; `BUG-12` is an entry in `defects/defects.yaml`:

```yaml
defects:
  - {id: BUG-12, title: Refund is one cent short, state: open, severity: high, labels: [billing]}
```

GitHub has no native severity: a `severity:high` label carries it, and the release policy's
`blocking_severities` reads it. Every adapter implements `get_states`, `list_open`,
`find_by_fingerprint` and `create` (`integrations.trackers.base.DefectTracker`).

## Status flowing back

```bash
python -m traceability defects sync           # known_defect ids from the suite + verdict ids
export DEFECT_STATUS_FILE=reports/traceability/defect-status.json
pytest ...
```

`sync` asks each tracker for the states of the ids the suite references plus every open
defect, once, and writes the snapshot. With `DEFECT_STATUS_FILE` set, `known_defect` and
`still_reproduces` treat the snapshot's closed ids as closed, exactly as if they were in
`KNOWN_DEFECTS_CLOSED` (which still adds to them). Conservative rules:

- a tracker that cannot be read (network, auth, rate limit) keeps the last snapshot's
  states and the sync prints a warning;
- an id never seen is open: a closed defect treated as open reports `cleared`, which
  fails loudly, while the reverse would hide a regression.

`DEFECT_SYNC=true` makes the controller sync once in `pytest_configure`, before workers
start (register `traceability.defects.register` before the known_defect plugin).

Plans (`max_open_defects`) and the release verdict read open defects from the same
snapshot; no snapshot is *no evidence*, never "zero open". Every consumer prints
`snapshot <date> (<n> days old)`.

Tracker reads go through a GET-only transport (`integrations/read_transport.py`) limited to
the tracker host and the issues/search paths; anything else is refused before a byte is
sent. A tracker with `get_states_batch` is asked in chunks; a rejected chunk is split in
half until the bad id stands alone, and that id lands in the snapshot's `missing` list.

### Committed snapshot and the offline check

```bash
python -m traceability defects refresh --keys-from declared   # writes defects/status-snapshot.json
python -m traceability defects check                          # offline, CI's offline job
```

`defects/tracking.yaml` says where detectors live and the floors. Declared ids are found
statically: `still_reproduces` / `known_defect` / `detector_pair.judge` call sites (id from
the first argument or `ticket=`, constants resolved) and `*.manifest.json` open issues.
`check` reads only the committed snapshot and fails on:

- a declared id with no entry ("refresh with `defects sync`");
- fewer call sites or ids than the floor, so a narrowed scan can't pass by tracking nothing;
- drift: a `closed=False` (or manifest) id the snapshot says is closed, or a `closed=True`
  id it says is open, unless the line is in `defects/drift-baseline.txt`; a baseline line
  that no longer drifts fails too.

No snapshot configured prints "not measured" and passes.

## Drafts flowing out

```bash
python -m traceability defects draft --from-run reports/               # drafts only
python -m traceability defects draft --from-run reports/ --submit       # file new ones
```

One draft per failed/errored test and per `closed_regressed` detector, written to
`reports/traceability/defect-drafts/<fingerprint>.md` plus `drafts.json`. The body
carries the node id, run id, failure message (from the failure bundle when there is one),
failed requests, the deployed build the run measured (or "not measured"), the CI run URL,
"Possibly already filed" (the defect ids the test links to, then up to three open snapshot
defects whose title shares three or more distinctive words with the failure) and a hidden
`<!-- defect-fingerprint: ... -->`. The body is redacted first: bearer/basic/JWT tokens,
`key=`/`token=`/`password=` values, credentials in URLs and the values of secret-named
environment variables become `<redacted>`. A failure whose detector reported `reproduces`
in the same run is a confirmed known defect and gets no draft.

The **fingerprint** is `sha256(nodeid + normalised failure message)[:16]`, where
normalising masks numbers, timestamps, UUIDs, hex ids and temp paths, so the same failure
maps to the same issue across runs. Before filing, the tracker is searched for the
fingerprint; an existing issue is reused, never duplicated.

Filing happens only with `--submit`, or in CI with `DEFECT_AUTOFILE=true` **and** a push
to `main`. Node ids passed with `--flaky file.json` are never filed: flaky tests make
noise, and dedup reduces it but cannot remove it.

## REST

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/defects?state=open&label=` | defect states from the last stored sync |
| GET | `/api/v1/defects/{id}` | state plus linked cases |
| POST | `/api/v1/defects/drafts` | `{run_id, nodeid}` of a stored failure → a draft (nothing filed) |
| PUT | `/api/v1/cases/{case_id}/defects/{id}` | an external framework links a case to a defect |

Filing over REST waits for the platform's writer roles; until then it is CLI-only.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DEFECT_TRACKERS_FILE` | `integrations/trackers.yaml` | prefix map |
| `DEFECT_STATUS_FILE` | — (`reports/traceability/defect-status.json` when written) | snapshot the verdict layer reads; unset = env var only |
| `GITHUB_TOKEN` / `DEFECT_TRACKER_TOKEN` | — | GitHub token (`issues: read`; `issues: write` to file) |
| `GITHUB_API_URL` | `https://api.github.com` | API base |
| `DEFECT_AUTOFILE` | `false` | allow CI filing on pushes to `main` |
| `DEFECT_SYNC` | `false` | sync on the controller at session start |
