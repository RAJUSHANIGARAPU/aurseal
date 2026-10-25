# Data health

A shared environment collects records that a later test or user will trip
over: dates a consumer cannot parse, references to things that no longer
exist, drafts left open, transactions a downstream system rejected. The
data-health scan finds them first. It reads the environment only, and it
reports findings rather than failing on them. The scanner, the rule factories
and the `GET`-only `GetOnlyApi` are described under *Data health* in
[oracles.md](oracles.md). This page covers the report and the runnable entry
point.

## Report with a separate "unverified" column

**What it does.** `ScanReport` (`oracles/data_health.py`) shows, per rule:

| Column | Meaning |
|---|---|
| Findings | Records the rule flagged |
| Unverified | Results the scan could not establish, for example because a referenced record could not be read. **Never counted as a finding, never as healthy.** |
| On automation-made records | Findings on records the suite's own accounts created |
| On others' records | Findings on records anyone else created |
| Creator unknown | Findings on records that do not say who created them |

The report also lists, per entity key, how many records were read; the `GET`
requests sent (the scan's cost on the environment); and the most frequent
creators. It includes bounded samples of findings and of unverified results.
When the scan stopped early, it carries the abort reason. A stopped scan says
in bold that it is **not a result**.

Keeping "unverified" apart matters. A draft whose age the API does not expose
is neither healthy nor a finding. Folding it into either count makes the
report claim something nobody measured.

**How to use it.** A rule reports an unverified result by returning
`Unverified(...)` instead of a plain string:

```python
from oracles import data_health as dh


def supplier_resolves(record):
    r = api.get(f"suppliers/{record['supplier_id']}")
    if r.status == 404:
        return [f"supplier {record['supplier_id']} does not resolve"]
    if not r.is_success:
        return [dh.Unverified(f"supplier lookup answered HTTP {r.status}")]
    return []


creator = lambda rec: rec.get("created_by")
scanner = dh.Scanner(
    dh.GetOnlyApi.over(api),
    [dh.Rule("dangling-supplier", "Suppliers whose reference is broken", supplier_resolves)],
    created_by=creator,
    is_automation=dh.accounts_predicate(["bot@example.test"], creator),
    automation_accounts=("bot@example.test",),
    sample_limit=5,
)
report = scanner.scan_all({"orders": "api/orders", "invoices": "api/invoices"})
print(report.to_markdown("qa"))
report.by_rule()["dangling-supplier"].unverified
```

`is_automation(record)` can be any predicate, for example one that checks a
tag field. `accounts_predicate` covers the common case: the creator field
names one of the suite's accounts (compared case-insensitively).
`report.to_dict()` gives the same content as JSON.

`scan_all` stops at the first key whose scan aborted. That happens on a 401
or 403, a 5xx, an unreadable or non-JSON listing, or a failed first page, so
a failing environment gets no further load. A page cap or a refused later
page leaves the report incomplete (`report.complete` is `False`) without
stopping it.

## Read-only scan entry point

**What it does.** `python -m oracles.data_health` runs an opt-in scan of the
configured entity keys. It writes `reports/data-health/<env>.md` and
`<env>.json`, and attaches the Markdown to Allure when it runs inside a test.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | The scan ran; findings are in the report and are not failures |
| `1` | A rule named in `DATA_HEALTH_FAIL_ON` has findings |
| `2` | The scan stopped; a scan that did not read the environment must not look clean |

Findings sit on shared data that someone else's flow created. A red build
would blame the wrong change, so a rule gates only when you name it.

The scan module never calls a writing verb. A test parses its source and
fails on any `post`/`put`/`patch`/`delete`/`request` call. The only door to
the environment is `GetOnlyApi`.

**How to use it.**

```bash
TEST_ENV=qa \
DATA_HEALTH_BASE_URL=https://api.example.test/v1/ DATA_HEALTH_TOKEN=... \
DATA_HEALTH_KEYS="orders=orders,invoices=billing/invoices" \
DATA_HEALTH_DATETIME_FIELDS=start,end DATA_HEALTH_BAD_STATUS="status=rejected|failed" \
DATA_HEALTH_DRAFT_FIELD=draft_id DATA_HEALTH_AUTOMATION_ACCOUNTS=bot@example.test \
python -m oracles.data_health --fail-on stale
```

From Python, pass your own rules and API:

```python
code = dh.main(["--env", "qa"], rules=my_rules, api=dh.GetOnlyApi.over(engine))
```

**Configuration.**

| Env var | Default | Meaning |
|---|---|---|
| `DATA_HEALTH_KEYS` | (required) | `key=listing/path` pairs, comma-separated; a bare `key` uses the key as the path |
| `TEST_ENV` / `--env` | `qa` | Environment name in the report and file name |
| `DATA_HEALTH_BASE_URL` | (required without `api=`) | API base URL |
| `DATA_HEALTH_TOKEN` | (unset) | Bearer token |
| `DATA_HEALTH_RULES` | `all` | Rule ids to run, comma-separated |
| `DATA_HEALTH_FAIL_ON` / `--fail-on` | (none) | Rule ids, or `all`, whose findings fail the run; an id not scanned is refused |
| `DATA_HEALTH_MAX_PAGES` | `10` | Page cap per key; hitting it marks the report incomplete |
| `DATA_HEALTH_PAGE_SIZE` | `100` | Listing page size |
| `DATA_HEALTH_AUTOMATION_ACCOUNTS` | (none) | The suite's accounts, for the creator split |
| `DATA_HEALTH_CREATOR_FIELD` | `created_by` | Record path naming the creator |
| `DATA_HEALTH_DATETIME_FIELDS` | (unset) | Enables `datetime-precision` and `date-without-time` on these paths |
| `DATA_HEALTH_FRACTION_DIGITS` | `3` | Expected fractional-second digits |
| `DATA_HEALTH_BAD_STATUS` | (unset) | `path=VALUE\|VALUE` reported as `bad-status` |
| `DATA_HEALTH_DRAFT_FIELD` | (unset) | UUIDv7 path; older than `DATA_HEALTH_DRAFT_MAX_AGE_HOURS` (default 48) is `stale` |

With no `rules=` argument and none of the rule variables set, the run fails
with a message naming them: a scan with no rules would report a clean bill of
health for nothing.
