# Test data lifecycle

The `test_data/` package covers where a test's data comes from, how it is
built, and how it is cleaned up afterwards.

## Cleanup ledger

**What it does.** Records everything a test creates, and discards it at
teardown even when the test fails. Items are discarded newest first. A discard
that raises, or that returns HTTP ≥ 400 (other than 404, which means the item
is already gone), is logged and reported. It never fails or masks the test.

**How to use it.** Load the fixtures with `-p test_data.pytest_plugin` or
`pytest_plugins = ["test_data.pytest_plugin"]`, then record items explicitly
or let the engine record them:

```python
from test_data.cleanup import CleanupLedger, CreationRule


def test_rename_order(api, cleanup_ledger):
    # explicit
    r = api.post("orders", json={"sku": "A-1"})
    cleanup_ledger.record("order", r.json()["id"], lambda: api.delete(f"orders/{r.json()['id']}"))


@pytest.fixture
def tracked_api(api):
    # automatic: every successful matching create is recorded
    ledger = CleanupLedger(
        [
            CreationRule(
                "order", "POST", r"/orders$", lambda i: api.delete(f"orders/{i}"), id_path="id"
            ),
        ]
    )
    api.observe(ledger.observer)
    yield api
    ledger.discard_all()
```

`CreationRule(kind, method, url_pattern, discard, id_path="id")` reads the new
id from a dotted path in the JSON body. With `id_path=None` it reads the id
from the `Location` header instead. `forget(kind, id)` drops an entry the test
disposed of itself. Items that could not be discarded show up in the test's
`user_properties` as `cleanup_failed`.

**Scheduled jobs.** `cancel_pending_jobs(list_jobs, cancel_job, belongs_to_test=..., jobs_path="items")`
cancels the jobs this test triggered that are still pending (`SCHEDULED`,
`RETRY_PENDING`, `PENDING`), so none fires into its data later. A
401/403 on the listing is reported once as `readable=False`.

**Drafts.** `open_draft(open_call, draft_id=..., discard=..., discard_stale=..., ledger=...)`
opens an exclusive draft. On a 409 it discards the stale draft and retries
once, then records the new draft in the ledger.

## Fixture-replay seeder

**What it does.** Builds data over the API by replaying an ordered JSON
fixture of requests. Placeholders are bound from live values. Any failure
raises `SeedError`, which names the step and quotes the response.

```json
{
  "name": "order-to-confirmed",
  "create": {"method": "POST", "path": "/orders",
             "body": {"customer": "${customer_id}", "placed": "${TODAY_TOKEN}"},
             "capture": {"order_id": "id"}},
  "steps": [
    {"method": "PUT", "path": "/orders/${order_id}?version=${version}",
     "body": {"due": "${RUN_DAY_DATE_PLUS_30D}"}},
    {"method": "POST", "path": "/orders/${order_id}/confirm", "optional": true}
  ]
}
```

```python
from test_data.fixture_replay import Fixture, FixtureSeeder

seeder = FixtureSeeder(api, version_field="version")
result = seeder.replay(Fixture.load("fixtures/order.json"), {"customer_id": "c-1"})
order_id = result["order_id"]
total = seeder.read_field("/orders/${id}", "total.net", {"id": order_id})
```

- `${name}` is looked up in this order: values captured from earlier
  responses, then the caller's params, then the built-in dates
  `TODAY_TOKEN`, `RUN_DAY_DATE_PLUS_<n>D` and `RUN_DAY_DATE_MINUS_<n>D`
  (in UTC). A string that is exactly one placeholder keeps the bound value's
  type.
- An unbound placeholder fails before anything is sent.
  `unbound_placeholders(fixture, provided)` runs the same check offline, so a
  renamed parameter fails in the offline suite.
- `capture` maps a name to a dotted path in the response. `"@location"`
  captures the id from the `Location` header.
- With `version_field` set, the entity version is threaded into the next call
  as `${version}`. A version that goes backwards fails.
- An `optional` step whose status is in `skip_statuses` (default
  400/404/409/422) is skipped, and the version carries forward unchanged.
- To replay onto an existing entity, pass its ids in params and
  `version=<current>`.

The syntax is `${...}` rather than the `{{...}}` used by
`DynamicDataEngine`: seeding must fail on a missing value, never fill it with
random data.

## Prerequisite factory

**What it does.** Provisions what a test needs, in dependency order, with a
per-run tag so parallel runs never collide. Each prerequisite is built once
per factory.

```python
from test_data.factory import Prerequisite, PrerequisiteFactory

factory = PrerequisiteFactory(
    seeder,
    [
        Prerequisite("pricelist", Fixture.load("fixtures/pricelist.json")),
        Prerequisite("customer", Fixture.load("fixtures/customer.json"), depends_on=("pricelist",)),
    ],
)
built = factory.provision("customer")  # builds pricelist first
customer_id = built["customer"]["id"]
```

Fixtures can use `${SEED_TAG}` (8 hex characters, or pass `seed_tag=`). They
can also use the captured values of each dependency, as `${<dependency>.<name>}`,
for example `${pricelist.id}`. `Prerequisite.after` runs a hook on the result,
for example to push the record through an approval step. Unknown names and
dependency cycles raise.

## Seed modes and target allowlist

**What it does.** Decides, in one place, where a data-dependent test gets its
entity.

| Mode | Behaviour |
|---|---|
| `fresh` (default) | Every test builds its own entity. Existing data is never listed. |
| `reuse` | Existing entities are picked by shape (restricted to the allowlist when one is set). The test builds one when the pool is empty. |
| `targets` | Only allowlisted entities are used. Building is refused, and the test skips with the reason. |

**Configuration.**

| Env var | Default | Meaning |
|---|---|---|
| `SEED_MODE` | `fresh` | `fresh` / `reuse` / `targets` (case-insensitive) |
| `ENTITY_ALLOWLIST` | (unset) | Inline allowlist: ids separated by commas, semicolons or whitespace |
| `ENTITY_ALLOWLIST_FILE` | (unset) | File allowlist; `#` starts a comment; merged after the inline ids, duplicates dropped |
| `SEED_REUSE_ENABLED` | `false` | Legacy switch for `reuse` |

The run fails at startup (`pytest.UsageError` from the `seed_config`
fixture) when:
- `targets` is set without an allowlist;
- an allowlist is set while the mode is `fresh`;
- the legacy reuse flag is set alongside another mode;
- the mode is unknown;
- an id is invalid;
- the allowlist file is unreadable, or adds no new id.

```bash
SEED_MODE=targets ENTITY_ALLOWLIST_FILE=targets.txt pytest -m api
```

## Reuse pool

```python
from test_data.pool import ReusePool, select_targets, NoTarget

pool = ReusePool(
    lambda: api.get("orders", params={"size": 100}).json()["items"],
    fit=lambda o: o["status"] == "active" and not o["locked"],
    label=lambda o: o["number"],
    preference=lambda o: o["line_count"],  # lowest first
)
try:
    targets = select_targets(seed_config, pool, seed=lambda: build_order(), limit=3)
except NoTarget as exc:
    pytest.skip(str(exc))
for t in targets:  # t.entity, t.label, t.was_seeded
    ...
```

`fresh` seeds one entity and never lists the pool. `reuse` takes up to
`limit` entities from the pool and seeds one only when the pool is empty.
`targets` takes allowlisted pool entities only and raises `NoTarget` when none
fits.

## Observed shape

`ObservedShape.observe(label, body, {"term": "billing.months", "lines": lambda b: len(b["lines"])})`
records what the test actually saw. An attribute that is unreadable or blank
stays `None`; it is never filled with a default. `require("term")` raises
`ShapeNotObserved` when an attribute is missing. `describe()` gives a one-line
log form, for example `order A-1 [term=3, lines=?]`.

## Entity resolution with ambiguity detection

**What it does.** Resolves a live record by name. When more than one
selectable record carries the requested name it fails loudly; it never picks
the first one. When the preferred name is gone it falls back to a current
record, preferring `fallback_prefix`, and logs that the hint is stale. When
nothing is selectable it raises `NothingSelectable`.

```python
from test_data.parties import LiveResolver

resolver = LiveResolver(
    lambda kind: api.get(f"{kind}", params={"size": 200}).json()["items"],
    selectable=lambda r: r["state"] == "active",
    fallback_prefix="Test ",
)
supplier = resolver.resolve("suppliers", "Example Supplies Ltd")  # AmbiguousMatch on duplicates
```

The caller supplies `name_of`, `id_of` and `selectable`. Results are cached
per kind and name for the life of the resolver.

## Data loader with environment overlay

**What it does.** Reads `ui/testdata/<key>.json` and deep-merges
`ui/testdata/<key>.<env>.json` over it when that file exists. Objects merge
recursively; scalars and arrays are replaced.

```python
from test_data import loader

data = loader.load("checkout", env="staging")
data.get("user.tier")  # KeyError names both files when missing
data.get("user.coupon", default=None)
```

| Setting | Default |
|---|---|
| `env` argument → `TEST_ENV` env var → | `qa` |
| `directory` argument | `ui/testdata/` |

Results are cached per (key, env, directory), and `get` returns copies.

## Fixture contract checker

**What it does.** Offline checks that catch a bad seed fixture before it
reaches an environment. Paths are dotted, and `[]` fans out over an array.

```python
from test_data import fixture_contract as fc

rules = [
    fc.Required("name"),
    fc.CarriesRunTag("name"),  # contains ${SEED_TAG}
    fc.When(
        "delivery",
        "post",
        "addresses[].purpose",
        lambda purposes: "postal" in purposes,
        "no postal address",
    ),
    fc.AtMost("addresses[].primary", 1, bool),
]
assert fc.check(fixtures, rules) == []
assert fc.unique_across(fixtures, "name") == []
```

## Case manifest

**What it does.** Turns a declared list of cases into parametrized tests. Each case has an
honest state: `automated` (names its `test`), `planned` (says what it is `waiting_on`) or
`manual` (gives `why_manual`).

```json
{"suite": "order-changes", "cases": [
  {"id": "OC-01", "title": "change quantity", "state": "automated",
   "test": "tests/test_orders.py::test_quantity", "seen_passing": true,
   "proof": "pytest tests/test_orders.py, two green runs, 2026-10-01",
   "inputs": {"quantity": 3}},
  {"id": "OC-02", "title": "change currency", "state": "planned",
   "waiting_on": "a second price list"},
  {"id": "OC-03", "title": "manual approval", "state": "manual",
   "why_manual": "needs a signature"}
]}
```

```python
from test_data.scenario_manifest import CaseManifest

M = CaseManifest.load("cases/order-changes.json")


@pytest.mark.parametrize("case", M.params())
def test_order_change(case): ...
```

A case that cannot be driven (manual, or planned and waiting) is skipped with its reason,
so the gap shows in every report. `summary()` counts `proven` cases apart from automated
ones: only cases that are automated **and** seen passing with a proof. Malformed cases fail
at load time, for example an automated case without `test`, `seen_passing` without
`proof`, an unknown state or a duplicate id. There is one schema; keys of another layout
are not read.

## Target allocation and claim ledger

**What it does.** Under `SEED_MODE=targets` several tests draw from one
allowlist. `TargetPicker` (`test_data/allocator.py`) hands each allowlisted
entity to at most one test per run. Two mutating tests on one entity would
each assert against a state the other changed.

The ledger works across `pytest -n auto` workers, because each worker is its
own process. A claim is a file under `reports/entity-allowlist/<run id>/claims/`
created with `O_CREAT | O_EXCL`, so exactly one worker wins. An entity whose
preflight marks it unsafe is set aside for the rest of the run (a file under
`rejected/`). Every claim and rejection is appended to
`reports/logs/entity-allowlist.log`, so "which test touched ORD-42?" has an
answer after the run.

`claim_for` is the one call a test makes. It:

1. skips identifiers already claimed or rejected;
2. resolves the rest to the reference the API needs through an injected,
   paged listing (`resolve_targets`). Reading stops when everything is found,
   a page is empty or last, a page is unreadable, a page repeats the previous
   one, or the page cap is reached;
3. runs the caller's preflight on each candidate in allowlist order;
4. claims the first that passes.

When nothing qualifies, the outcome explains each identifier: claimed by
which test, rejected and why, unsuitable for this test, or not found.

**How to use it.**

```python
from test_data.allocator import ListingPage, PreflightResult, TargetPicker, claim_for

allocator = TargetPicker()  # one per worker is fine; the files are shared


def page(n):
    r = api.get("orders", params={"page": n, "size": 100})
    if not r.is_success:
        return None
    body = r.json()
    return ListingPage([(o["number"], o["version_id"]) for o in body["items"]], body["last"])


def preflight(number, version_id):
    order = api.get(f"orders/{version_id}").json()
    if order["draft_open"]:
        return PreflightResult(False, "open draft", unsafe=True)
    return PreflightResult(order["status"] == "active", f"status {order['status']}")


outcome = claim_for(
    allocator,
    seed_config.allowlist.identifiers,
    request.node.name,
    lookup_page=page,
    preflight=preflight,
)
if outcome.claim is None:
    pytest.skip(outcome.explanation)
```

**Configuration.**

| Setting | Default | Meaning |
|---|---|---|
| `ENTITY_ALLOWLIST_MAX_PAGES` | `20` | Listing pages read before resolution gives up |
| `TEST_RUN_ID` | resolved by `core.run_id` | Keys the ledger directory; set it when one run spans workers on several machines |
| `ledger_root` / `log_path` (constructor) | `reports/entity-allowlist` / `reports/logs/entity-allowlist.log` | Where claims and the log go; `log_path=None` disables the log |

## Pool replenishment

**What it does.** Keeps an allowlist stocked. `replenish(count, seed)`
(`test_data/pool.py`) builds `count` entities through a caller-supplied
seeder and writes their labels to a targets file in the allowlist format, so
a later run consumes them with `SEED_MODE=targets ENTITY_ALLOWLIST_FILE=<file>`.
With `booked=True` the seeder is asked for entities whose first period is
already booked.

A failed seed is recorded in `result.failures` and the next one is tried.
Replenishment fails only when nothing was seeded. It refuses to run under a
`targets` seed config, because that mode promises no data is created.

**How to use it.**

```python
from test_data.pool import replenish, replenish_settings

count, booked = replenish_settings()
result = replenish(
    count,
    lambda booked: seed_subscription(booked=booked),
    booked=booked,
    label=lambda s: s["number"],
    config=seed_config,
)
print(result.path, result.seeded, result.failures)
```

```bash
SEED_MODE=targets ENTITY_ALLOWLIST_FILE=reports/entity-allowlist/replenished.txt pytest -m api
```

**Configuration.**

| Env var | Default | Meaning |
|---|---|---|
| `REPLENISH_COUNT` | `5` | Entities to seed |
| `REPLENISH_BOOKED` | `false` | Seed entities with the first period booked |
| `path` (argument) | `reports/entity-allowlist/replenished.txt` | Targets file written |
