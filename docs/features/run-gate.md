# Run gate

`gate/` answers a question the tests themselves don't ask: **is this run evidence at all?**
A build that selected 40 tests, executed 8 and skipped 32 exits 0 and shows green. The run
gate turns that state into a verdict. Around it sit the execution policies that keep a run
honest: lanes, fail-fast, a circuit breaker, and a skip-or-fail switch.

All of it is offline-testable (`pytest tests/gate`) and works under `pytest -n auto`
(pytest-xdist) and `--reruns` (pytest-rerunfailures).

Registration: the root `conftest.py` calls `gate.pytest_plugin.register(config)`. Anywhere
the root conftest is not loaded, use `pytest -p gate.pytest_plugin`.

---

## Run trust

### What it does

At session end the gate tallies every selected test. The tally runs on the xdist controller,
from the reports the workers send, and a rerun attempt is replaced by the final one. Four
independent rules then judge the run:

| Rule | Fails when | Applies only when |
|---|---|---|
| Executed floor | fewer than `min_executed` tests produced an answer (passed, failed, errored, xfailed) | the run *selected* at least `min_executed` tests (a narrow selection is not a shrunken run) |
| Skip ratio | more than `max_skip_ratio` of the selection abstained (skipped, or selected but never reported) | something was selected |
| Detector verdicts | fewer than `min_verdicts` tracked defects reached a verdict | at least one detector (expected-red test) was selected |
| Fixture provenance | fewer than `min_acquired` passing tests stood on data they actually acquired | at least one `@pytest.mark.seeded` test was selected |

A rule with nothing to judge is listed as **absent**, which is not the same as passing. If no rule applies, the
verdict is `UNJUDGED`, never `PASS`. A rule is disabled by its threshold: `0` for the
counts, `>= 1.0` for the ratio.

Detector verdicts come from two sources:

- the verdict rows in `VERDICTS_FILE` for this run. These count as reached: `reproduces`,
  `cleared`, `closed_pass`, `closed_regressed`. These are listed as silences:
  `held_empty`, `signature_mismatch`;
- the `known_defect` marker's own outcome: xfailed (reproduces) or XPASS(strict) (cleared).

Fixture provenance comes from the rows `gate.provenance.record_fixture` writes (see
[Fixture provenance](#fixture-provenance)).

### Enforcement levels

`RUN_TRUST` turns the gate on and decides what a `FAIL` verdict does:

| Value | Effect of a FAIL verdict |
|---|---|
| unset | gate off: no tally, no marker (a developer running one test is not held to a gate's bar) |
| `fail` | the run's exit status becomes 1 (only when it would otherwise have been 0) |
| `unstable` | exit status unchanged; `python -m gate.integrity` emits a CI warning |
| `report` | exit status unchanged; verdict still printed and written |

Lowering enforcement never turns the finding off. In every mode the verdict is computed,
printed as a boxed "run trust" section in the terminal summary, and written to the
markers. `--collect-only` sessions are never judged.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RUN_TRUST` | unset | `fail` \| `unstable` \| `report` |
| `RUN_TRUST_MIN_EXECUTED` | `10` | executed floor; `0` disables |
| `RUN_TRUST_MAX_SKIP_RATIO` | `0.50` | skip ceiling; `>= 1.0` disables |
| `RUN_TRUST_MIN_VERDICTS` | `1` | detector verdicts required; `0` disables |
| `RUN_TRUST_MIN_ACQUIRED` | `1` | passing tests on acquired data required; `0` disables |
| `RUN_TRUST_DIR` | `reports` | where markers are written |
| `RUN_TRUST_LANE` | `default` | lane name; non-default lanes get `run-trust-<lane>.*` |
| `RUN_TRUST_DETECTOR_MARKERS` | `known_defect` | comma-separated detector markers |
| `RUN_TRUST_SEEDED_MARKERS` | `seeded` | comma-separated markers for the provenance rule |
| `VERDICTS_FILE` | `reports/verdicts/verdicts.jsonl` | detector verdict rows (read only) |

An unparseable value falls back to its default rather than aborting the run.

### Markers

- `reports/run-trust.txt`: the first line is machine-readable, followed by the boxed summary:
  ```
  VERDICT=FAIL ENFORCEMENT=FAIL RUN=20260101T120000Z-1a2b3c4d LANE=default
  ```
- `reports/run-trust.json`: the numbers, thresholds, breaches, absent rules and every
  test's final outcome.

The markers are written on every gated run, including passes, so a missing marker means the
gate did not run.

### Using it in CI

```bash
RUN_TRUST=fail pytest tests/ -n auto
python -m gate.integrity                 # prints the box; exit 1 on an enforced FAIL
python -m gate.integrity --lane offline  # a named lane's marker
python -m gate.integrity --allow-missing # exit 0 when no marker exists
```

Exit codes of `python -m gate.integrity`:

- `0`: pass, not assessed, `unstable` or `report`. Under `unstable` on GitHub Actions it
  prints a `::warning`.
- `1`: FAIL with `fail` enforcement.
- `2`: no marker. The gate never ran, which must not read as a pass.

### Example

```bash
$ RUN_TRUST=fail RUN_TRUST_MIN_EXECUTED=5 pytest -m smoke
...
| RUN TRUST FAILED - THIS RUN IS NOT EVIDENCE                         |
| selected 12 . executed 3 . skipped 9 (75%)                              |
| SKIP RATIO: 75% abstained (9 of 12) - required at most 50%              |
|     move the bar with RUN_TRUST_MAX_SKIP_RATIO                      |
| what was skipped, and why:                                              |
|     SKIPPED  tests/test_search.py::test_filters                         |
|         search service not reachable                                    |
```

---

## Rerun-safe run ids

Every process in a run shares one run id, held in `TEST_RUN_ID`: the controller, each xdist
worker, and any later invocation that belongs to the same run. Verdict rows, fixture rows,
the circuit-breaker trip file and the integrity markers all carry it.

- If `TEST_RUN_ID` is set, it is used as-is. In CI, set it to something stable across the
  invocations of one run, for example `${{ github.run_id }}-${{ github.run_attempt }}`.
- If it is absent, the controller generates one (`<UTC timestamp>-<8 hex>`) during
  `pytest_configure` and exports it before any worker starts. Workers inherit it and also
  receive it through xdist's `workerinput`.
- **A later invocation of the same run merges and doesn't replace.** If `pytest --lf` runs
  with the same `TEST_RUN_ID` and lane, the retried tests' new outcomes replace their old
  ones, and the verdict is recomputed over the whole run. A one-test retry can therefore
  never overwrite a forty-test verdict. A marker from a different run id or lane is ignored
  and replaced.
- Within one invocation, a pytest-rerunfailures `rerun` attempt is discarded and only the
  final attempt counts.

```bash
export TEST_RUN_ID=build-118-1
RUN_TRUST=fail pytest -m regression          # 1 flaky failure
RUN_TRUST=fail pytest -m regression --lf     # verdict recomputed over all tests
```

---

## Lanes and markers

`gate/lanes.py` holds the execution policy as code. Markers are registered at runtime, so
they also work under `--strict-markers`.

| Marker | Meaning | Default gate |
|---|---|---|
| `unit` | framework self-tests, own lane, run first | always excluded |
| `on_demand` | harness/config validations, not product signal | excluded unless `INCLUDE_MANUAL_CHECKS=true` |
| `slow` | long-running checks | excluded unless `RUN_SLOW_TESTS=true` |
| `e2e` | end-to-end against a shared backend | included, run sequentially |
| `stateful` | mutates shared backend state | included, run sequentially |
| `seeded` | needs data it acquired (provenance rule) | included |
| `journey_group(group)` | fail-fast group (see below) | included |
| `draft` | generated test awaiting review | always excluded (drafts job) |
| `render_check` | document rendering behind a bearer token | always excluded (render job, [CI workflow](ci-workflow.md#token-only-lane)) |

Detector (expected-red) tests are never excluded: their verdicts are what the gate reports
on.

The policy:

- **Unit lane first.** A broken harness fails fast and cheaply, and the gate's report then
  covers product flows only.
- **Sequential by default for state-sharing tests.** `e2e` and `stateful` run with `-n 0`
  unless `E2E_PARALLEL_SAFE=true`.
- **One reported rerun** in the gate lane.
- **Never mask an exit status.** pytest exits 5 when nothing is selected, and that is
  exactly what a marker typo produces. `|| true` and `|| [ $? -eq 5 ]` are flagged by the
  [workflow lint](#workflow-lint).

```python
from gate.lanes import gate_command, gate_marker_expression, unit_lane_command

unit_lane_command()  # ['pytest', '-m', 'unit', '-n', 'auto']
gate_marker_expression("smoke", {})
# '(smoke) and not unit and not on_demand and not slow'
gate_command(env={})
# ['pytest', '-m', 'not unit and not on_demand and not slow', '-n', '0', '--reruns', '1']
```

---

## Journey-group fail-fast

If a step shared by many page objects breaks, all tests going through that flow fail
in the same place. Tests join a group explicitly:

```python
import pytest

pytestmark = pytest.mark.journey_group("checkout")


def test_pay_by_card(checkout_page): ...
def test_pay_by_voucher(checkout_page): ...
```

How it works:

- On the first **deterministic** failure in a group, the remaining peers are skipped with
  the reason `journey group 'checkout' already failed at checkout_page.py:submit:42 (first
  seen in ...)`.
- The first failing test keeps its full diagnostics and its reruns.
- The fingerprint is the deepest traceback frame under a page-object path. A failure with
  no such frame, such as a plain assertion in the test body, never gates the group.
- Timeouts don't gate the group: a timeout against a shared environment signals load, not
  a broken step. They are logged and peers keep running.
- Only a *final* failure counts. An attempt pytest-rerunfailures will retry gates nothing.
- Every grouped test also gets `xdist_group("journey_group:<group>")`. With
  `--dist loadgroup` the whole group runs on one worker, so fail-fast holds across it.
  Without that, state is per worker.

| Variable | Default | Meaning |
|---|---|---|
| `FAILFAST_JOURNEY_GROUP` | unset | `true` enables skipping; when unset, skipping is on if `CI` is set |
| `JOURNEY_GROUP_TOLERATE_TIMEOUTS` | `true` | `false` lets timeouts gate the group too |
| `FAILFAST_PAGE_PATHS` | `ui/pages/` | comma-separated path fragments that identify page-object frames |

Failures are always recorded; only the skipping is switched.

---

## Circuit breaker

### What it does

The first 5xx response from the configured environment trips the breaker for the rest of
the run. Every later test then **abstains** (skips before sending traffic), which avoids a
wall of red that says nothing about the product and keeps load off a failing environment.

- Only the configured hosts count. Third-party hosts and failed connections never trip it.
- The first trip reason is kept. Across xdist workers the trip is shared through a run-id
  file (`reports/circuit-breaker/<run id>.tripped`), so every worker abstains.
- Abstentions are skips, so the [skip-ratio rule](#run-trust) reports a tripped run as
  not evidence instead of letting it pass.

### Usage

```python
from gate.circuit_breaker import default_breaker


def test_rejects_malformed_order(circuit_breaker, api):
    # This test provokes a 500 on purpose: declare it, scoped to this thread and block.
    with circuit_breaker.expect_server_error("/orders/submit", "malformed payload"):
        response = api.submit_order({"items": None})
    assert response.status_code == 500


# Feed responses from a client or a browser listener:
default_breaker().record("browser", "GET", "https://shop.example.test/cart", 503)
```

An upstream dependency timing out behind the environment can be exempted up to a limit:

```python
from gate.circuit_breaker import ServerErrorBreaker, problem_detail_upstream_timeout

breaker = ServerErrorBreaker(
    ["https://api.example.test"],
    upstream_timeout=problem_detail_upstream_timeout("/lookups/"),
    upstream_limit=3,  # the 4th upstream read timeout trips it anyway
)
```

`gate.circuit_breaker.assume_environment_not_tripped()` lets any helper abstain explicitly.

| Variable | Default | Meaning |
|---|---|---|
| `CIRCUIT_BREAKER` | `true` | `false` disables it |
| `CIRCUIT_BREAKER_HOSTS` | the `--env` base URL host | comma-separated hosts (or URLs) that count |
| `CIRCUIT_BREAKER_UPSTREAM_LIMIT` | `3` | upstream read timeouts tolerated per process |

---

## Gateway retry policy

`gate/http_retry.py` is a pure policy that decides whether a 5xx is re-sent. Browser route
handlers and API clients use the same policy.

- A `500` is never retried. It is the server's own answer.
- A `502/503/504` is retried only on a call that is safe to send twice: a read
  (`GET`/`HEAD`/`OPTIONS`), or a write the server deduplicates (by default, one carrying
  `?version=N`, whose duplicate is refused with 409).
- Streaming and download endpoints are never taken over.
- If a fetch fails after a write was sent, the request is **aborted**, never resumed, so a
  write is never sent twice.
- Each discarded 5xx goes to `on_discard` (wire it to the circuit breaker). A gateway error
  that is still there after the last retry is `ABSTAIN`: deliver it, and let the breaker
  decide.

```python
from gate.http_retry import RetryPolicy, fetch_with_retry

policy = RetryPolicy.from_env()
outcome = fetch_with_retry(
    lambda: session.get(url),
    "GET",
    url,
    policy,
    status_of=lambda r: r.status_code,
    on_discard=lambda attempt, status: breaker.record("api", "GET", url, status),
)
outcome.decision  # Decision.DELIVER | Decision.ABSTAIN
```

| Variable | Default | Meaning |
|---|---|---|
| `HTTP_RETRY_5XX` | `3` | retries; `0` disables |
| `HTTP_RETRY_BACKOFF_S` | `1.5` | linear backoff unit (seconds × attempt) |

---

## Skip-or-fail requirement

In the default gate, an environment problem is a skip. In a lane whose whole purpose is to
run a suite, or to produce a verdict, a skip-everything run exits 0 and is indistinguishable
from success. There, not running must fail. Each lane opts in with its own variable, so one
lane can never arm another.

```python
from gate.requirement import require_or_skip, require_verdict_or_skip


def test_render_invoice(renderer):
    require_or_skip(renderer.reachable(), "renderer not reachable")
    ...


def test_discount_rule_covered(fixture_pool):
    if not fixture_pool.has("expired-voucher"):
        require_verdict_or_skip("CASE-12", "no expired voucher available")
```

| Variable | Default | Effect when true |
|---|---|---|
| `SUITE_REQUIRED` | `false` | `require_or_skip` fails instead of skipping |
| `VERDICT_REQUIRED` | `false` | `require_verdict_or_skip` fails instead of skipping |

Use `gate.requirement.Requirement("MY_LANE_REQUIRED")` to create a lane-specific switch.

---

## Config binding to an external checkout

Some tests check claims against an external checkout, for example a configuration repository
read at a pinned ref. By default an absent checkout means abstain, and a `required` variable
turns that abstention into a failure. Resolution never raises, and its diagnostic names the
case that needs fixing: setting absent, malformed path, missing directory, or a
half-populated checkout.

```python
from gate.requirement import require_checkout


def test_routes_match_config():
    root = require_checkout(
        "CONFIG_CHECKOUT",  # where the checkout is
        "CONFIG_BINDING_REQUIRED",  # true => fail instead of skip
        expected=("flows", "settings.yml"),
        fallback="../shop-config",
    )
    ...
```

---

## Read-only client and preflight

### Read-only client

Code that inspects a shared environment must not be able to change it. `ReadOnlyClient`
exposes only `get` and `head`. It keeps a closure over the session's `get`, not the session
itself, and it is immutable. `find_write_calls` scans source for `.post/.put/.patch/.delete`
(and `request("POST", ...)`), so a test can keep modules read-only:

```python
import requests
from gate.read_only import ReadOnlyClient, find_write_calls

client = ReadOnlyClient.over(requests.Session(), "https://api.example.test/v1")
client.get("/orders/42", timeout=10)


def test_inspection_code_cannot_write():
    assert find_write_calls(["inspection/"]) == []
```

### Preflight

Before a test writes to an existing record picked from a shared environment, two questions
are asked:

- **Safety**: would writing be harmful or meaningless (open draft, cancelled owner, expired)?
  An unsafe record is set aside for the whole run (`SET_ASIDE`).
- **Suitability**: is it the shape this test needs? An unsuitable record is left for other
  tests (`NOT_SUITABLE`).

A check that cannot decide is `UNKNOWN`, and unknown safety is treated as unsafe.
`run_preflight` reads only through a `ReadOnlyClient`.

```python
from gate.preflight import CheckSpec, Disposition, Kind, run_preflight

SPECS = [
    CheckSpec("no open draft", Kind.SAFETY, lambda s: not s["order"]["draft_open"]),
    CheckSpec(
        "fully paid", Kind.SUITABILITY, lambda s: s["order"]["balance"] == 0, need="fully_paid"
    ),
]
report = run_preflight(client, "ORD-42", {"order": "/orders/42"}, SPECS, needs=["fully_paid"])
if report.disposition is not Disposition.USE:
    pytest.skip("; ".join(report.reasons()))
```

---

## Fixture provenance

`record_fixture` appends one JSONL row for each piece of test data a test is handed. The row
records whether the data was built fresh from an owned fixture or reused from the
environment's pool. Rows carry the run id and are attributed to the running test through
`PYTEST_CURRENT_TEST`, which is correct on every xdist worker. Recording never raises.

```python
from gate.provenance import record_fixture


@pytest.mark.seeded
def test_refund(order_factory):
    order = order_factory.create()
    record_fixture("fresh", "orders/basic.json", entity_id=order.id)
    ...


@pytest.fixture(scope="module")
def shared_customer(request, pool):
    customer = pool.pick()
    # A module-scoped acquisition covers every test in the module.
    record_fixture("reused", "customer-pool", nodeid=request.node.nodeid, candidates=5)
    return customer
```

| Variable | Default |
|---|---|
| `DATA_ORIGIN_FILE` | `reports/data-origin.jsonl` |

---

## Workflow lint

GitHub Actions workflows only ever run on the CI service, so a broken edit is usually found
after it has already blinded CI. `gate.workflow_lint` checks the cheap, decidable properties:

- each file parses as a mapping with `on` and `jobs`;
- each job has `runs-on` (or `uses`) and steps, and each step has exactly one of `run`/`uses`;
- no `env` value is empty (`KEY:` with no value is YAML null);
- every `${{ ... }}` expression is closed;
- each `needs` entry names an existing job;
- no pytest command masks its exit status;
- required workflows and jobs are present.

```python
from gate.workflow_lint import lint_workflows


def test_workflows_are_sane():
    findings = lint_workflows(".github/workflows", {"tests.yml": ["offline"]})
    assert findings == [], "\n".join(map(str, findings))
```

`tests/gate/test_workflow_lint.py` runs this against the repository's own workflows.

---

## Test-data key audit

Every key a test reads must exist in every data file it may read. A key missing from one
environment's overlay otherwise fails only in the lane nobody watches.

`gate.data_keys.audit` scans the test sources for subscript chains such as
`test_data["search"]["keyword"]` and for `.get("key")` calls without a default. It then looks
each chain up in the base file and in the base merged with each overlay:

- `.get("key", default)` is exempt;
- a non-literal key (`test_data[name]`) is reported as **unresolvable**, never silently
  dropped.

```python
from gate.data_keys import audit


def test_every_data_key_exists():
    result = audit(
        ["ui/tests", "api/tests"],
        ["ui/testdata/test_data.json"],
        names=("test_data",),
        overlays=["ui/testdata/test_data.staging.json"],
    )
    assert result.refs, "no test-data reference found"
    assert result.ok, result.describe()
```

`tests/gate/test_data_keys.py` runs this audit over the repository's real test data.

---

## Bring-up branches

A test that has never run must not be able to redden the gate before someone has watched
it pass once. A bring-up branch gives that test one watched run, on its own.

Push to `bringup/<test path>` and the bring-up job runs only that selection:

- every marker exclusion is dropped (`-m ""`), so `on_demand`, `slow` and `unit` cannot
  filter the named test out, and every opt-in toggle (`INCLUDE_MANUAL_CHECKS`,
  `RUN_SLOW_TESTS`) is set to `true`;
- no reruns, so a flake shows up instead of being retried away;
- sequential (`-n 0`), because the test's isolation is not known yet;
- `COVERAGE_VERDICT_REQUIRED=true`, so a `no_verdict` abstention fails the run;
- pytest's exit status is returned unchanged. Exit 5 ("selected nothing") is never masked.

Git forbids `:` and `*` in branch names, so `/` stands in for `::` after the first segment
that ends in `.py`:

| Branch | Selection |
|---|---|
| `bringup/tests/api/test_orders.py` | `tests/api/test_orders.py` |
| `bringup/tests/api/test_orders.py/TestTotals/test_tax` | `tests/api/test_orders.py::TestTotals::test_tax` |
| `bringup/tests/api` | the directory |
| `bringup/test_orders` | the one file named `test_orders.py` (an error if there are none or several) |
| `bringup/` | an error: an empty suffix names no test |

`BRINGUP_SELECTION` overrides the branch. It takes one or more space-separated node ids,
path globs (`tests/api/test_*.py`) and a trailing `*` on the test name, which becomes
`-k <prefix>`. `-k` matches substrings, so that selection can be wider than a prefix.

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `GITHUB_REF_NAME` | set by GitHub Actions | the branch `python -m gate.lanes bringup` reads when `--ref` is not given |
| `BRINGUP_SELECTION` | unset | explicit node ids or globs; wins over the branch name |

### Example

```bash
git push origin HEAD:bringup/tests/api/test_orders.py/test_tax

# what the job will run, without running it
python -m gate.lanes bringup --ref bringup/tests/api/test_orders.py/test_tax --dry-run
# pytest tests/api/test_orders.py::test_tax -m '' -n 0
```

```python
from gate.lanes import bringup_command, bringup_node_id

bringup_node_id("bringup/tests/api/test_orders.py/test_tax")
# 'tests/api/test_orders.py::test_tax'
bringup_command("bringup/tests/api/test_orders.py", env={})
# ['pytest', 'tests/api/test_orders.py', '-m', '', '-n', '0']
```

The workflow step runs `python -m gate.lanes bringup`, not a literal `pytest` line, so the
CI-selection gate (`tests/test_ci_selection.py`) sees no extra selection to account for.

---

## Local workflow parse check and commit guard

The [workflow lint](#workflow-lint) also runs from the command line, so a broken workflow
fails on the developer's machine, before it is pushed:

```bash
python -m gate.workflow_lint                      # every workflow under .github/workflows
python -m gate.workflow_lint .github/workflows/tests.yml --actionlint
```

| Exit | Meaning |
|---|---|
| 0 | every file parsed and passed the structure checks |
| 1 | findings (parse error or structure problem), one line each |
| 2 | a named file does not exist |
| 3 | `--actionlint` was asked for and actionlint is not installed. This is an abstention and is reported as one, never as green |

`gate.workflow_lint.parse_check(path)` returns the same statuses as a `CheckResult`.
`actionlint_check(paths)` runs actionlint when it is on `PATH`.

**Commit guard.** `.pre-commit-config.yaml` registers two hooks:

- `actionlint` (the upstream hook) for the full workflow schema and expression checks;
- `workflow-lint`, a local hook scoped to `^\.github/workflows/.*\.ya?ml$` that runs
  `python -m gate.workflow_lint` on every staged workflow and blocks the commit on a
  finding.

Both are plain pre-commit hooks, so they work the same from a terminal, an IDE or any
other tool that commits.

### Configuration

| Setting | Default | Effect |
|---|---|---|
| `--dir` | `.github/workflows` | directory scanned when no files are given |
| `--actionlint` | off | also run actionlint; exit 3 when it is missing |

### Example

```bash
pre-commit install
git add .github/workflows/tests.yml
git commit -m "ci: add a job"     # blocked if the workflow no longer parses
```
