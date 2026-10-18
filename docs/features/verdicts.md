# Verdicts: detectors, coverage verdicts and the run record

A green build answers one question: did anything fail? It cannot say what the
run proved. A skipped detector exits 0 like a confirmed one. A test that only
wrote a figure down looks the same as one that asserted it. The verdict layer
in `verification/` makes those states explicit and countable:

| Module | What it gives you |
|---|---|
| `verification/defect_detector.py` | `still_reproduces`: a detector for an open defect, with a positive control |
| `verification/pytest_plugin.py` | the `known_defect` marker, now with `match=` and verdict recording |
| `verification/evidence_verdict.py` | `EvidenceVerdict`: proved / recorded / held_empty / abstained |
| `verification/fix_scope.py` | one oracle, asserted where a fix is deployed, a detector elsewhere |
| `verification/known_defects_manifest.py` | open issues as JSON data, with gate-safety checks |
| `verification/verdicts.py` | the JSONL verdict record and `read_verdicts` |
| `verification/status_report.py` | the end-of-run status file and lane health |
| `verification/outcome_classifier.py` | observations in, one worded verdict out, for checks with more than two answers |

All of it registers through the existing `register(config)` call in the root
`conftest.py`. Outside this repository, use `-p verification.pytest_plugin`.

---

## 1. Detector with a positive control: `still_reproduces`

### What it does

A detector wraps an oracle that asserts the **correct** behaviour of a defect
that is still open. While the defect is open the oracle fails. `still_reproduces`
inverts the *reporting*, never the assertion:

| What happened | Verdict | Test result |
|---|---|---|
| control failed or skipped | `held_empty` | skip |
| oracle skipped, or raised a non-assertion error | `held_empty` | the original exception propagates |
| oracle failed, message matches the signature | `reproduces` | **pass** |
| oracle failed, message does not match | `signature_mismatch` | fail |
| oracle passed (defect open) | `cleared` | fail, `KNOWN DEFECT CLEARED` |
| defect closed, oracle passed | `closed_pass` | pass |
| defect closed, message matches the signature | `closed_regressed` | fail |

Three rails stop a detector from turning into a permanent false green:

- **Positive control (required).** A detector concludes "still broken" from the
  *absence* of correct behaviour. A harness break produces the same absence:
  a read that returned `""`, or a page that never loaded. `exercised` must
  prove the mechanism really ran before any verdict counts. A failing control
  reads as "could not tell", never as "still reproduces". The argument is
  keyword-only and has no default. Pass `exercised=None` only together with
  `no_control_reason="..."` explaining why the oracle cannot be fooled by a
  no-op.
- **Signature tightness.** The signature is a regex (DOTALL) that the
  assertion message must contain. Once defect ids and punctuation are removed,
  it must still have at least 12 characters. A signature that is only `BUG-142`
  would also match setup failures that carry the same prefix, so it raises
  `ValueError`. Key it to the wording of the verdict assertion itself.
- **Cleared is a failure.** A green "it is fixed" would sit in a report
  forever. A red one makes someone verify the fix, close the defect and drop
  the wrapper, so the oracle remains as an ordinary regression test.

`attribute_failure(message, defect=[...], environment=[...])` sorts a rejection
from the system under test into `DEFECT`, `ENVIRONMENT` or `UNATTRIBUTABLE`
before a detector turns it into a verdict. Environment patterns win even when
the message echoes the defect's field.

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `KNOWN_DEFECTS_CLOSED` | unset | comma-separated defect ids that are closed (see section 2) |

### Example

```python
from decimal import Decimal

from verification.defect_detector import still_reproduces

SIGNATURE = r"BUG-142: the discount was not applied to the order total"


def assert_discount_applied(order: dict) -> None:
    expected = sum(Decimal(x) for x in order["lines"]) - Decimal(order["discount"])
    actual = Decimal(order["total"])
    assert actual == expected, (
        f"BUG-142: the discount was not applied to the order total ({actual} != {expected})"
    )


def assert_order_was_read(order: dict) -> None:
    assert order.get("id") and order.get("total"), "order read back empty"


def test_discount_bug_still_reproduces():
    order = {"id": "ORD-1", "lines": ["10.00", "5.00"], "discount": "1.50", "total": "15.00"}
    still_reproduces(
        "BUG-142",
        SIGNATURE,
        lambda: assert_discount_applied(order),
        exercised=lambda: assert_order_was_read(order),
    )
```

When the fix lands, the oracle passes and the test fails with
`KNOWN DEFECT CLEARED`. Remove the `still_reproduces` wrapper and call
`assert_discount_applied(order)` directly.

### The marker form

For a detector without a control, the `known_defect` marker accepts the same
signature as `match=`:

```python
import pytest


@pytest.mark.known_defect(
    "BUG-142",
    reason="discount ignored on totals",
    raises=AssertionError,
    match=r"discount was not applied to the order total",
)
def test_discount_applied():
    raise AssertionError("BUG-142: discount was not applied to the order total")
```

A failure that does not match `match=` is reported as a plain failure, not an
xfail. A pattern that is too loose is a usage error at collection time. Every
marked test records a verdict, the same as `still_reproduces`.

---

## 2. Closed defects: stricter than "stay green"

A defect id listed in `KNOWN_DEFECTS_CLOSED` is expected to be **fixed**.
Both `still_reproduces` and the `known_defect` marker then require the oracle
to pass:

- oracle passes: `closed_pass`, the test is green;
- the defect reproduces (signature matches): `closed_regressed`, the test
  **fails** with `EXPECTED-RED REGRESSED ON A CLOSED DEFECT`;
- any other failure: `signature_mismatch`, the test fails.

This is deliberately stricter than a detector that stays green whenever the
defect is observed. Once a defect is closed, a reproduction is a regression,
and it turns the build red instead of only being worded differently in a
report. A detector can still be green in this state: when the fix holds.

```bash
KNOWN_DEFECTS_CLOSED=BUG-142,BUG-150 pytest tests/ -n auto
```

`still_reproduces(..., closed=True|False)` overrides the variable for a single
call.

---

## 3. Coverage verdicts: `EvidenceVerdict`

### What it does

A coverage test proves that a behaviour is present and correct. Four outcomes
all exit 0, and only one of them is release evidence:

| Method | Verdict | Meaning |
|---|---|---|
| `proved(evidence)` | `proved` | exercised and asserted. **The only release evidence.** |
| `recorded(figure)` | `recorded` | observed and written down, not asserted |
| `held_empty(what)` | `held_empty` | a check held vacuously because its data was absent; the test continues |
| `no_verdict(reason)` | `abstained` | a precondition could not be arranged; the test skips |

`proved` rejects blank evidence. Write the observed values, never "OK".
Abstain only for a precondition the test cannot arrange itself. Anything the
test sets up is an assertion, not an abstention.

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `COVERAGE_VERDICT_REQUIRED` | unset | `1`/`true`/`yes`/`on`: `no_verdict` **fails** instead of skipping |

### Example

```python
from verification.evidence_verdict import EvidenceVerdict


def test_order_total_is_the_sum_of_its_lines():
    order = {"id": "ORD-7", "lines": [4.00, 8.40], "total": 12.40}
    verdict = EvidenceVerdict("REQ-12 order totals")
    if not order["lines"]:
        verdict.no_verdict("no order with lines in this environment")
    assert round(sum(order["lines"]), 2) == order["total"]
    verdict.proved(f"total {order['total']} = sum of {len(order['lines'])} lines on {order['id']}")
```

---

## 4. Fixed on one environment, expected-red elsewhere: `fix_scope.judge`

### What it does

A fix reaches one environment before the others. `judge` reads one oracle in
two ways:

- **where the fix is expected** (`env` is in `fixed_on`): the control runs, the
  oracle is asserted positively, and a pass records `proved` plus a `cleared`
  detector verdict (`closed_pass` if the defect is closed). A failing oracle
  fails the test and records `reproduces` (or `closed_regressed` /
  `signature_mismatch`);
- **everywhere else**: the oracle runs as a `still_reproduces` detector.

In both readings, a failing control is an abstention, never a verdict.

### Configuration

The list of environments comes from a variable you choose, read with
`fix_scope_envs(variable, default)` (comma separated). The active environment is
passed in explicitly. In this repository that is the `--env` option.

### Example

```python
from orders_checks import assert_discount_applied, assert_order_was_read  # section 1 helpers

from verification.evidence_verdict import EvidenceVerdict
from verification.fix_scope import fix_scope_envs, judge


def test_discount_fix(request):
    order = {"id": "ORD-1", "lines": ["10.00", "5.00"], "discount": "1.50", "total": "13.50"}
    judge(
        request.config.getoption("--env"),
        fix_scope_envs("DISCOUNT_FIX_ENVS", default="staging"),
        "BUG-142",
        r"BUG-142: the discount was not applied to the order total",
        coverage=EvidenceVerdict("BUG-142 discount"),
        control=lambda: assert_order_was_read(order),
        oracle=lambda: assert_discount_applied(order),
        evidence=lambda: f"discount applied on {order['id']}",
    )
```

(`orders_checks` stands for wherever you keep the two helpers from section 1.)

```bash
DISCOUNT_FIX_ENVS=staging,qa pytest -m regression --env=qa
```

---

## 5. Open-issue manifests

### What it does

Some suites check many similar templates (documents, screens, exports) against
one set of finding ids. For those, what is known to be wrong can live in JSON
next to the data instead of in code. Put one `*.manifest.json` file per family
in a directory; each file holds a JSON list of entries:

```json
[
  {
    "template": "monthly-statement",
    "absent_from_envs": ["staging"],
    "samples": ["samples/monthly-statement.complete.json"],
    "open_issues": [
      {"issue": "ISS-310", "summary": "sum drift", "notes": "total one cent high",
       "seen_in": "sample 3, row 2", "finding": "SUM-DRIFT"},
      {"issue": "ISS-311", "summary": "wording", "notes": "old footer text"}
    ]
  }
]
```

There is one schema; keys from any other layout are not read. An entry may
carry extra keys for its own suite (the document templates add `pinned_text`,
`must_show`, `section_headings`, `money_fields` and `conditional_lines`); the
loader keeps them in `entry.data`.

**An open issue never makes a check pass.** `finding` decides what an issue
does:

- **detector** (`finding` names a finding id): that finding is excused from
  the main check *and* asserted to keep occurring. When it stops, the entry
  reports `cleared` and the test fails;
- **note** (no `finding`): documentation only. It is quoted in the failure
  message and excuses nothing.

`judge_findings(entry, observed, observable)` splits one template's findings.
`.assert_clean()` fails on any finding no detector explains, and on any
detector that cleared. A detector whose finding id is outside `observable`
(this observer cannot produce it) gets **no verdict**: an absence the observer
could not have seen is not reported as a fix.

`check_manifest(...)` returns every gate-safety problem; an empty list means
the manifest is sound:

- a template named by two entries;
- an issue without an issue id or summary, or a detector without `seen_in`;
- a `finding` that no check emits (`emitted_kinds`);
- a detector whose oracle input field is empty (`oracle_inputs`), so it can
  never fire;
- an `absent_from_envs` value outside `known_envs`;
- a sample that is missing or is not valid JSON;
- detectors reaching half or more of all open issues (`max_excused_share`):
  the excused set must stay a short quarantine list, not a policy.

`detector_split(entries)` returns `(asserted, noted)`, so notes are never
counted as coverage.

### Document finding ids

The document oracles (`oracles/document_findings.py`, `oracles/template_structure.py`)
emit these ids, so a `finding` names one of them:

| Id | Meaning |
|---|---|
| `UNFILLED-SLOT` | template syntax printed instead of a value |
| `NULL-TEXT` | `null`, `None`, `undefined`, `NaN` where a value belongs |
| `DANGLING-LABEL` | a `must_show` label with nothing after it |
| `DANGLING-HEADING` | a section heading with nothing under it |
| `ABSENT-VALUE` | a `pinned_text` string that appears nowhere |
| `RAW-AMOUNT` | a money line not put through the money format |
| `BAD-DELIMITER` | a template expression with broken delimiters |
| `UNKNOWN-FILTER` | a format the renderer does not know |
| `UNDECLARED-PATH` | a template reads a path the payload schema does not declare |
| `UNREADABLE` | a template file whose text cannot be read |

### Example

```python
from pathlib import Path

from verification.known_defects_manifest import (
    by_template,
    check_manifest,
    judge_findings,
    load_manifest,
)

MANIFESTS = Path(__file__).parent / "manifests"
KINDS = {"SUM-DRIFT", "EMPTY-CELL"}


def test_manifest_is_gate_safe():
    entries = load_manifest(MANIFESTS)
    assert check_manifest(entries, emitted_kinds=KINDS, known_envs={"qa", "staging"}) == []


def test_monthly_statement():
    entry = by_template(load_manifest(MANIFESTS), "monthly-statement")
    observed = ["SUM-DRIFT"]  # what your checker found on the rendered statement
    judge_findings(entry, observed, observable_kinds=KINDS).assert_clean()
```

---

## 6. The verdict record (JSONL contract)

### What it does

Every detector and coverage outcome appends one JSON line to a single file.
Other tools, such as a run-trust gate, read this file. Treat the format as
a contract and keep it stable.

- **File:** `$VERDICTS_FILE`, default `reports/verdicts/verdicts.jsonl`.
- **Writes:** appended, one object per line, one `write` per record on an
  `O_APPEND` handle. xdist workers can share the file without interleaving
  partial lines.
- **Never raises on I/O:** a reporting problem is logged and never changes a
  test's outcome. An unknown `kind`, or a verdict outside that kind's
  vocabulary, raises `ValueError`, because that is a caller bug.

| Field | Type | Meaning |
|---|---|---|
| `run_id` | str | `TEST_RUN_ID` at record time, `""` when unset |
| `nodeid` | str | pytest node id of the test that produced the verdict |
| `kind` | str | `expected_red` or `coverage` |
| `ticket` | str or null | the defect id, or the optional ticket a coverage subject traces to |
| `verdict` | str | see below |
| `detail` | str | the message behind the verdict, whitespace-flattened, at most 500 characters |
| `subject` | str | extra: what the verdict is about (the ticket, or the coverage subject) |
| `timestamp` | str | extra: UTC ISO-8601 |

Readers must ignore keys they do not know.

- `expected_red` verdicts: `reproduces`, `cleared`, `held_empty`,
  `signature_mismatch`, `closed_pass`, `closed_regressed`.
- `coverage` verdicts: `proved`, `recorded`, `held_empty`, `abstained`.

```json
{"run_id": "1234-1", "nodeid": "tests/test_orders.py::test_discount_bug_still_reproduces", "kind": "expected_red", "ticket": "BUG-142", "verdict": "reproduces", "detail": "BUG-142: the discount was not applied to the order total (15.00 != 13.50)", "subject": "BUG-142", "timestamp": "2024-05-01T10:00:00+00:00"}
```

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `VERDICTS_FILE` | `reports/verdicts/verdicts.jsonl` | the record file |
| `TEST_RUN_ID` | `""` | run id stamped on every record; set one per CI run |

### Reading it

```python
from verification.verdicts import read_verdicts

records = read_verdicts(run_id="1234-1")  # a missing file returns []; malformed lines are skipped
cleared = [r["ticket"] for r in records if r["verdict"] == "cleared"]
```

`read_verdicts(path=None, run_id=None, *, start=0)`: `start` is a byte offset,
so a session can read only the lines appended since it began.
`suppress_recording()` is a context manager for the machinery's own
self-tests, so synthetic verdicts never reach the real file.

---

## 7. End-of-run status file

### What it does

At the end of a run, the xdist controller (or the only process) writes a
Markdown status document built from that run's records. It is generated, not
maintained by hand. Records belong to the run when they carry the current
`TEST_RUN_ID`, or, without one, when they were appended after the session
started. No file is written when there is nothing to report. The status file
never fails a run; enforcement belongs to whatever gate reads the records.

The document has three sections, each leading with the number a green build
hides:

- **Tracked defects:** one row per defect id, aggregated by severity, not by
  arrival order (`signature_mismatch > closed_regressed > reproduces >
  held_empty > cleared > closed_pass`). A defect reports `cleared` only when
  *every* record for it cleared. If no detector gave a verdict, the section
  says `NO DETECTOR GAVE A VERDICT`.
- **Coverage verdicts:** the best outcome per subject. If nothing was proved,
  the section says `NO COVERAGE VERDICT GIVEN`.
- **Lane health:** covers tests carrying a lane marker (default `seeded`),
  meaning tests that must provision their own data before they can assert
  anything. Reruns collapse onto the best attempt. When no lane test passed,
  one of four headlines names what went wrong:
  `EVERY LANE ASSERTION FAILED`, `LANE ERRORED BEFORE ASSERTING`,
  `LANE PROVISIONED BUT ASSERTED NOTHING` or `LANE PROVISIONED NOTHING`.
  A lane test files its provisioning receipt with `mark_provisioned`.

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `VERDICT_STATUS_FILE` | `reports/verdicts/verdict-status.md` | where the status document is written |
| `VERDICT_LANE_MARKERS` | `seeded` | comma-separated markers tallied as the lane |
| `TEST_RUN_ID` | `""` | scopes the report to one run (otherwise: records appended since session start) |

### Example

```python
import pytest

from verification.status_report import mark_provisioned


@pytest.mark.seeded
def test_new_order_is_invoiced(request):
    order = {"id": "ORD-9", "status": "invoiced"}  # stand-in for data your test provisions
    mark_provisioned(request.node)  # only after provisioning really succeeded
    assert order["status"] == "invoiced"
```

```bash
TEST_RUN_ID=local-1 pytest tests/ -n auto
cat reports/verdicts/verdict-status.md
```

---

## 8. Outcome classifiers: observations in, one worded verdict out

### What it does

Some checks can end in more than two ways, and the additional endings are the
risky ones. `assert refusal_shown` gives one and the same red for "the input could
not be edited at all" and "the product let a forbidden change through". The first
shows nothing; the second is exactly the defect the check is there to catch.

`verification/outcome_classifier.py` separates observation from judgement. The
page object or client records what it saw into an observations object. A
classifier turns that into exactly one named `Verdict`. The decision is pure:
it runs offline and every branch is unit-tested.

Each verdict state carries two flags:

| Flag | Meaning |
|---|---|
| `is_verdict` | the run exercised the subject, so the outcome says something about the product. `False` is an abstention |
| `passed` | the single outcome that may be reported as a pass |
| `demands_follow_up` | `is_verdict and not passed`: someone has to read the message |

`Verdict.report(coverage)` turns a verdict into a test outcome. An abstention
goes to `EvidenceVerdict.no_verdict` (skip, or fail under
`COVERAGE_VERDICT_REQUIRED`). A pass is recorded as `proved`. Anything else
raises `AssertionError` with the worded message.

Three classifiers ship with the base `OutcomeClassifier`:

**`PriceSurfaceClassifier`** pins a free-form pricing screen and fires when it
stops being free-form. The proof is a pair: an arbitrary price is accepted, and
zero is refused with the floor rule's own message. Branch order is the
argument. Reachability comes first, then whether anything was read, then a new
price surface (it outranks every price outcome), then the price pair, and
drift last.

| State | Verdict? | Meaning |
|---|---|---|
| `NO_PRICE_SURFACE` | pass | every pinned property held |
| `PRICE_SURFACE_APPEARED` | yes | a second plan value or a price-table-shaped field exists |
| `ZERO_PRICE_ACCEPTED` | yes | zero went through and no refusal appeared |
| `ZERO_PRICE_REFUSAL_DID_NOT_BLOCK` | yes | the refusal appeared but the flow advanced |
| `ZERO_PRICE_REFUSED_BY_SOMETHING_ELSE` | yes | zero was held without the floor message |
| `ARBITRARY_PRICE_REFUSED` | yes | something now constrains the price |
| `DESCRIPTION_OUTDATED` | yes | editability, the plan value or the add-on list moved |
| `SCREEN_NOT_REACHED`, `BLOCK_NOT_READ`, `PRICE_PROBE_NOT_RUN` | no | abstentions |

Labels in `PriceSurfaceExpectation.lookalikes` (such as `Schema version`)
contain a surface token but are known not to be one. They are filtered out by
exact normalised name ahead of token matching, since wrongly concluding "the
feature exists" is the most harmful mistake a characterisation can make.

**`step_outcome`** crosses "was the required block filled?" with "did the step
advance or get blocked?": `FILLED_AND_ADVANCED`, `FILLED_BUT_BLOCKED`,
`EMPTY_AND_BLOCKED`, `EMPTY_AND_BLOCKED_WITHOUT_THE_MESSAGE`,
`EMPTY_BUT_ADVANCED`, plus four `NO_VERDICT_*` states. The classifier infers a
block only from a positive rejection signal: the message, an HTTP status of
400 or higher, or a validation surface. "Did not advance" on its own is what a
broken harness produces, so it gives `NO_VERDICT_SUBMIT_RESULT_UNKNOWN`.
`expected_step_state(resolves)` maps configuration to the predicted state.
`join_block_values` treats a value that only echoes its own label as empty.

**`refusal_outcome`** judges a forbidden change inside an edit flow. Only
`REFUSED` passes. `ACCEPTED_WITHOUT_NOTICE`, `STOPPED_WITHOUT_MESSAGE` and
`REJECTED_YET_PROCEEDED` are product failures. `INPUT_LOCKED` and
`CHANGE_IGNORED` are abstentions. A change counts as taken only when the new
value contains the target *and* differs from the old value.

### Configuration

| Parameter | Default | Effect |
|---|---|---|
| `PriceSurfaceExpectation(...)` | `Standard` plan, three add-ons, `Price must be greater than 0.` | the pinned characterisation, the surface tokens and the lookalikes |
| `step_outcome(..., rejection_message=)` | `An address is required.` | the message that attributes a block to the empty field |
| `refusal_outcome(..., expected_message=)` | `This value cannot be changed.` | compared verbatim, never as a loose substring |
| `COVERAGE_VERDICT_REQUIRED` | unset | makes `report()` fail on an abstention instead of skipping |

### Example

```python
from verification.evidence_verdict import EvidenceVerdict
from verification.outcome_classifier import RefusalObservations, refusal_outcome


def test_billing_term_cannot_change_mid_edit():
    observed = RefusalObservations(  # filled by the page object
        target="monthly",
        value_before="yearly",
        value_after="monthly",
        field_editable=True,
        refusal_shown=True,
        advanced=False,
    )
    refusal_outcome(observed, expected_message="Term is locked.").report(
        EvidenceVerdict("REQ-28 locked billing term")
    )
```
