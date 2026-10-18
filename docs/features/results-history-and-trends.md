# Results history and trends

Trends answer "is this test getting worse, and since when?" from the results store. The
code lives in `results/trends.py`; every question is a named SQL query in
`results/queries/` plus a small computation, shared by the CLI, the REST handlers and the
dashboard. CI history survives ephemeral runners as run bundles on a `results-history`
branch (`results/history.py`).

## Per-case trend

**What it does.** For one case over its last N runs (`window`, default 30):

| Measure | Rule |
|---|---|
| pass rate | passed / (passed + failed + error). `skipped`, `not_run` and `xfailed` are counted and shown, never in the denominator. No executions means **unmeasured**, never 100% |
| flips | pass <-> fail transitions between consecutive executed runs |
| streak | the current run of passes or failures, and `failing_since`: the first failing run since the last pass |
| retry-masked passes | runs where the final attempt passed after a failed attempt |
| duration | p50 and p95 (nearest rank); drift = median of the last 5 / median of the 20 before - 1, flagged above `RESULTS_DURATION_DRIFT` (default 0.5 = +50%) |
| revision boundaries | runs where the case's revision changed (see case versioning) |

A case with parameter rows has one outcome per run: any failure or error fails it, else
any pass passes it, else xfailed, else skipped, else not run.

**Unsound runs.** A run the run-trust gate judged FAIL (it executed too little to be
evidence) is excluded from case windows by default and counted in `excluded_unsound`;
`include_unsound=True` (CLI `--include-unsound`) puts it back. This is the "absence of
measurement is not success" rule: a run that executed nothing must not raise a pass rate.

## Suite trend

Per run, oldest first: selected, executed, passed, failed, xfailed, skipped, not run,
passes that needed a retry, executed ratio (executed / max(selected, expected count)),
wall time and the integrity verdict, filterable by lane and branch. Every pass rate is
printed with its denominator, for example `92% (46/50 executed, 12 skipped)`.

## Run compare

`compare_runs(store, run_id, baseline_run_id=None)` puts every test of a run in one
bucket against a baseline (default: the previous finished run on the same project, lane
and branch): **new failures**, **fixed**, **still failing**, **still failing for a
different reason** (the failure fingerprint changed), **newly skipped or not run**,
**new tests**, **removed tests**. With no stored baseline it says so instead of
reporting an empty, green-looking comparison.

## Top lists

- `unstable(store, window=30, min_flips=2)` — cases that flip at least `min_flips` times,
  or passed only on a retry, over the last `window` sound runs.
- `slowest(store, window=30, top=20)` — cases by median duration.

## Flakiness detector on store history

`results.adapters.flakiness.history_from_store(store, window)` yields the existing
flakiness detector's `FlakRecord`s (non-final attempts become `rerun`), and
`grouped_history(store, window)` feeds `FlakinessDetector.get_profiles(groups=...)`
directly. The detector's own JSONL history keeps working unchanged.

## Command line

```bash
python -m results trends case c-1f2e3d4c5b6a7980 [--window 30] [--include-unsound]
python -m results trends suite --lane offline [--branch main] [--window 30]
python -m results trends unstable | slowest [--window 30] [--lane offline]
python -m results compare "$TEST_RUN_ID" [--baseline <run_id>] [--format md|json]
```

## REST handlers

| Method | Path | Purpose |
|---|---|---|
| GET | `/cases/{case_id}/history?limit=` | outcome history for one case |
| GET | `/cases/{case_id}/trend?window=&include_unsound=` | computed trend |
| GET | `/trends/suite?lane=&branch=&window=` | per-run suite trend |
| GET | `/runs/{run_id}/compare?baseline=` | run compare |
| GET | `/trends/unstable`, `/trends/slowest` | top lists |

## History across CI runs: the `results-history` branch

A runner's database dies with the job, so CI keeps the record as compressed run bundles
on an orphan branch and treats the database as a cache:

```
results-history (orphan branch)
├── README.md
└── bundles/<YYYY-MM>/bundle-<run_id>.ndjson.gz
```

```bash
python -m results history add --dir .results-history --run-id "$TEST_RUN_ID"   # from the store
python -m results history add --dir .results-history --bundle reports/results/bundle-*.ndjson
python -m results history rebuild --dir .results-history     # import every bundle (idempotent)
python -m results history prune --dir .results-history --keep-runs 2000
```

Bundles are gzipped with a fixed timestamp, so the same run always produces the same
bytes. `rebuild` reports a corrupt bundle and exits 1 instead of hiding it. The Python
code never runs git: checking the branch out, committing and pushing are CI steps. The
workflow snippet (a job on pushes to `main`, with `contents: write` and a concurrency
group so two runs never push at once):

```yaml
  results-history:
    needs: [offline, browser]
    if: always() && github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    concurrency: results-history
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - uses: actions/download-artifact@v4
        with:
          pattern: results-*
          path: artifacts/
      - name: Check out the results-history branch (created on first use)
        run: |
          if git ls-remote --exit-code --heads origin results-history; then
            git fetch --depth=1 origin results-history
            git worktree add .results-history FETCH_HEAD
            git -C .results-history switch -c results-history
          else
            git worktree add --detach .results-history
            git -C .results-history switch --orphan results-history
          fi
      - name: Add this run's bundles and rebuild the database
        run: |
          python -m results history add --dir .results-history \
            --bundle $(find artifacts -name 'bundle-*.ndjson')
          python -m results history rebuild --dir .results-history
          python -m results compare "$TEST_RUN_ID" --format md >> "$GITHUB_STEP_SUMMARY"
      - name: Commit and push the history
        run: |
          cd .results-history
          git add README.md bundles
          git -c user.name="results-history" -c user.email="results-history@users.noreply.github.com" \
            commit -m "results: run $TEST_RUN_ID" || echo "nothing new"
          git push origin results-history
```

Until that job runs, CI history is per job: the compare step then reports "no baseline".
Pull requests read the history (fetch the branch, `history rebuild`) but never push it.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RESULTS_DURATION_DRIFT` | `0.5` | drift ratio above which a duration change is flagged |
| `RESULTS_DB` | `reports/results/results.db` | the store the trends read |

## Limits

Trends are only as long as the persisted history. Retry-masked flakiness is visible only
because every attempt is stored; running with `--reruns` and the recorder off loses it.
