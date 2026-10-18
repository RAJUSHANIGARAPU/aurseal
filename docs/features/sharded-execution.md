# Sharded execution and result merge

## What it does

Splits one test selection across N CI machines (xdist still parallelises inside each
machine), then merges the shards back into one run and proves nothing was lost.

- **Planning** (`gate.shard.plan_shards`): longest-processing-time greedy bin packing
  over per-test durations. The plan is a pure function of its inputs, ties are broken
  by a stable hash of the node id, so every shard computes the same plan on its own
  without coordination. A test with no recorded duration gets the median of the known
  ones (1 s when none are known). Tests marked `e2e` or `stateful` are packed as one
  unit and land on the same shard: there is no per-test state isolation, and splitting
  them across machines would race them on shared records.
- **Selection** (`gate.pytest_shard`, loaded with `-p gate.pytest_shard`): adds
  `--shard=K/N` and deselects every item outside shard K. A bad value (`4/3`) is a
  usage error, exit 4, never an empty run.
- **Evidence per shard** (in `--shard-dir`, default `reports/shards`):
  `shard-K-of-N.selected.json` (full collection + this slice + planned seconds),
  `shard-K-of-N.executed.json` (node ids that reported + exit status),
  `shard-K-of-N.durations.json` (measured seconds, the next plan's input), and one
  `metric_shard_run` row (planned vs actual seconds, selected vs executed).
- **Merge** (`gate.merge`): JUnit XML files summed into one `<testsuites>`; Allure
  result directories copied into one with `environment.properties` unioned and
  `categories.json` de-duplicated; shard durations combined into
  `reports/durations.json`.
- **Completeness** (`gate.merge.check_complete`): every declared shard left results,
  no shard selected or executed zero tests, every selected test reported, the shards
  collected the same test set, and the union of executed tests equals the full
  collection. Any gap fails the merge.
- **CI-selection gate**: `--shard`, `--shard-durations`, `--shard-dir` and
  `-p gate.pytest_shard` are execution-only, so the gate replays a sharded command as
  the unsharded selection it splits.

## How to use

```bash
# One shard of three
pytest -m regression -n auto -p gate.pytest_shard --shard=2/3

# Preview a plan
pytest -m regression --collect-only -q | python -m gate.shard --plan 3

# Merge (exit 1 when incomplete)
python -m gate.merge --manifests reports/shards --shards 3 \
    --junit reports/junit-*.xml --allure reports/allure-*/ \
    --junit-out reports/junit-merged.xml --allure-out reports/allure-results
python -m gate.integrity --lane browser
```

## Configuration

| Option / variable | Default | Meaning |
|---|---|---|
| `--shard=K/N` | off | Run shard K of N |
| `--shard-durations` / `SHARD_DURATIONS` | `reports/durations.json` | `{nodeid: seconds}` used to balance |
| `--shard-dir` | `reports/shards` | Where the manifests go |
| `METRICS_SINK` | `jsonl` | `jsonl`, `store` or `off` for the `metric_shard_run` row |
| `METRICS_DIR` | `reports/metrics` | JSONL metric location |

Durations can also come from the results store (`gate.shard.durations_from_store`,
named query `case_durations`).

## Example

```text
$ python -m gate.merge --manifests reports/shards --shards 3
merge: INCOMPLETE
  - shard 3/3 left no results
  - 41 collected test(s) ran on no shard, e.g. tests/test_orders.py::test_cancel
```

## Honest limits

- Sharding buys nothing for a small suite: each machine pays setup (browser install
  ~1-2 min). Enable a matrix only when the lane exceeds ~10 minutes, and N>3 is
  rarely worth it on hosted runners.
- Pinning all `e2e`/`stateful` tests to one shard means that shard is at least as
  long as those tests together.
