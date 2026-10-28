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
- **Serial lane** (`--serial-lane` on every K/N shard, then `--shard=serial/N`): tests
  not shown to be isolated leave the parallel shards and run in one pass after all of
  them have finished — tests marked `serial`, the sequential lanes (`e2e`, `stateful`,
  unless `E2E_PARALLEL_SAFE`), and, when `--isolated-markers` / `SHARD_ISOLATED_MARKERS`
  names some markers, every test carrying none of them. The serial pass may select
  nothing; that exits 0. Its manifests are `shard-serial-of-N.*`.
- **Merge and the serial lane**: the serial lane counts as a shard (what it selected must
  report; a test the shards handed to it must not vanish), but an empty serial lane has
  no say. `--summary` prints one line per shard and a verdict that is `PASS` only when
  the run is complete, every shard with a say exited 0 and no JUnit case failed.
- **Worker prefix**: each shard exports `TEST_SHARD` (`2/3`, `serial/3`) and, unless set,
  `TEST_WORKER_PREFIX` (`w2`, `ws`). Seeded test-data names carry the prefix
  (`core.run_id.data_tag`), so two shards never mint the same name and a leftover
  record names its worker. A malformed prefix (not `[a-z0-9]{0,8}`) fails.

## One login user per worker

Parallel workers sharing one login end each other's sessions, and every failed attempt
counts against the one account the identity provider watches. `gate.user_pool` gives
each test process a user of its own:

- **Pool file**: `.env.<env>.pool`, git-ignored, next to `.env.<env>`, lines
  `POOL_<n>_USERNAME=` / `POOL_<n>_PASSWORD=` (`n` orders the pool; gaps are fine; a
  username without a password, or the reverse, is an error).
- **Lease**: an OS file lock per user in `USER_POOL_LEASE_DIR` (default
  `~/.cache/aurseal/leases`), held for the life of the process and released by the OS
  when it ends, crash included. The lock file names the holder (pid, start, shard, cwd).
  Shard `i/N` asks for pool user `i` first (its cached session), then any free one; when
  all are leased it waits `USER_POOL_WAIT_S` (600 s, polling every `USER_POOL_POLL_S`, 5 s)
  and then fails naming the usernames and the lock directory, never a password.
- **Precedence**: an exported `AUTH_USERNAME` wins and the pool is not touched; then the
  pool; then `.env.<env>` and `environments.json` as before. No pool file, no change.
- **A rejected password stops the process** (`ui.components.universal_login`): a 400
  answer to the password step's POST (`LOGIN_PASSWORD_STEP_PATTERN`) latches the
  username, and every later login as it in the process fails at once without opening a
  page. Retrying per test is how one wrong password locks an account.

## Local parallel run

`scripts/run-parallel.sh [--env E] [--workers N] [--on-host] [--headless] [-- pytest args]`
runs the CI gate selection (`gate.parallel_run`) as N shard processes, then the serial
lane, then `gate.merge --summary`, and prints the verdict, one line per shard and which
pool user each shard leased (and how many shards each user served). By default it runs
inside `scripts/run-in-container.sh`: the Playwright image CI uses, a headed browser on a
virtual display (nothing opens on your screen; sites that refuse headless user agents
still work), the checkout at `/work`, and one named volume for the lease directory and
the token cache. Workers default to min(pool size, 4) and never exceed the pool. The
executed floor (`RUN_TRUST_MIN_EXECUTED`) is split over the shards, rounded up; the
serial lane gets 0. Every run starts from an empty `reports/parallel/`.

## How to use

```bash
# One shard of three
pytest -m regression -n auto -p gate.pytest_shard --shard=2/3

# Preview a plan
pytest -m regression --collect-only -q | python -m gate.shard --plan 3

# Serial lane: every shard with --serial-lane, then the serial pass
pytest -m regression -p gate.pytest_shard --shard=1/2 --serial-lane
pytest -m regression -p gate.pytest_shard --shard=2/2 --serial-lane
pytest -m regression -p gate.pytest_shard --shard=serial/2

# All of it locally, one pool user per worker, in the Playwright container
scripts/run-parallel.sh --env qa --workers 3

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
| `--serial-lane` | off | Move tests not shown to be isolated to the serial pass |
| `--isolated-markers` / `SHARD_ISOLATED_MARKERS` | none | Markers that show a test is isolated |
| `TEST_WORKER_PREFIX` | `w<K>` / `ws` when sharded | Prefix of seeded data names |
| `USER_POOL_LEASE_DIR` | `~/.cache/aurseal/leases` | Pool lock files |
| `USER_POOL_WAIT_S` / `USER_POOL_POLL_S` | `600` / `5` | Wait for a free pool user |
| `USER_POOL_LEASE_RECORD` | unset | File the process writes its leased user to |
| `LOGIN_PASSWORD_STEP_PATTERN` | `/login/password/?$` | Path regex of the password POST |
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
- The serial lane is as long as its tests together, and it starts only after the
  slowest shard; a large serial lane gives back most of what sharding saved.
- The pool caps parallelism at the number of users, by design. The lock is advisory and
  per machine: two machines sharing a pool need separate pools.
- The rejected-password stop only knows the definite answer (400 on the password step).
  An identity provider that answers a wrong password with 200 and an error page is not
  detected; set `LOGIN_PASSWORD_STEP_PATTERN` or it keeps the old behaviour.
- The container runs the browser the image ships (Chromium); CI's gate uses branded
  Chrome (`--browser-channel=chrome`), so a Chrome-only difference shows up in CI first.
