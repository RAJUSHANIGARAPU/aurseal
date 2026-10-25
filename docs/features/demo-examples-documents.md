# Demo examples: documents

Worked examples that render and read real PDFs from the demo system under test
(`demo_sut`, see [demo-sut.md](demo-sut.md)) and judge them with the document
oracles ([oracles.md](oracles.md)): template structure for every template,
rendered text against the ledger, overlay banners, and content detectors that
are run against both states of a demo fault switch.

All examples are API level and run offline in `pytest tests/ -n auto` (each
xdist worker starts its own demo on a localhost port). They live in
`tests/demo_examples/documents/`:

| File | What it shows |
|---|---|
| `oracles.py` | content oracles scoped to the *Details* section, money on a document |
| `_support.py` | seeding a subscription with its documents, reading their text |
| `fixtures/` | invented seed values (`seed-content.json`) and a change request |
| `templates/` | template manifests, complete and minimal samples, payload schema |

The detector examples write their verdicts to a per-test file
(`tests/demo_examples/documents/conftest.py`), because they produce a
deliberate `cleared` verdict that must not land in the run's evidence. The
fault set active during each test is recorded by `demo_control` as the
`demo_faults` user property and is on every demo response (`X-Demo-Faults`).

## Document service client

**What it does.** `api.clients.document_service.DocumentServiceClient` is a thin
client over `HttpEngine` for a document-render service: list the deployed
templates, download a template's source bytes, render a template with a payload
to PDF, and read the documents the system issued on its own (metadata, PDF,
stored file). A 2xx body that is not a PDF raises `NotAPdf`; a non-2xx raises
`ApiError`.

**How to use it.** Build it on the engine for the service's base URL. Paths are
relative to that URL; pass a `DocumentPaths` when the service names them
differently.

**Configuration.** No environment variables of its own; the engine's settings
apply (`API_TIMEOUT_S`, run tagging, auth). `DocumentPaths` defaults:
`templates`, `templates/{id}/source`, `render`, `documents/{id}`,
`documents/{id}.pdf`, `files/{id}`, `subscriptions/{id}/documents`.

**Example.**

```python
from api.clients.document_service import DocumentPaths, DocumentServiceClient
from api.clients.http_engine import HttpEngine

docs = DocumentServiceClient(
    HttpEngine("https://docs.example.test/api/"),
    DocumentPaths(render="documents/render"),
)
pdf = docs.render("invoice", {"invoice": {"number": "ORD-42", "total": 12.5}})
```

Against the demo, the `documents` fixture gives the client on `demo_http`
(`test_document_pipeline.py::test_document_service_client_against_demo`).

## Render and storage round trip

**What it does.** Seeds a subscription with a run tag, then checks that every
document the demo issued was created during this run (its `run_tag`), is tagged
with a template the service lists, and that the document PDF and the stored file
are byte-identical with the size and SHA-256 the file metadata records. A direct
render of a template also returns a readable PDF.

**How to use it.** `pytest tests/demo_examples/documents/test_document_pipeline.py`.

**Configuration.** None. The run tag is generated per test and sent as
`X-Test-Run-Id` by a `DemoClient(base_url, run_tag=...)`.

**Example.**

```python
for doc in documents.documents_of(sub["id"]):
    assert documents.document_pdf(doc["id"]) == documents.file(doc["file_id"])
```

## Template manifests and samples

**What it does.** `templates/billing.manifest.json` and
`templates/notices.manifest.json` describe every template the demo deploys: its
samples, the text that must appear (`pinned_text`), the labels that must carry a
value (`must_show`, `section_headings`), the money lines (`money_fields`), the
lines that only print when their value is there (`conditional_lines`) and where
the template is not deployed (`absent_from_envs`). `test_template_renders.py`
then checks, per template:

* **coverage**: `template_structure.manifest_coverage` - every deployed template
  has an entry, and `check_manifest` finds no unsound one;
* **structure**: `template_structure.validate_template` over the template source
  against `templates/payload.schema.json` - no broken delimiter, no path the
  schema does not declare. A separate test requires the schema to declare
  exactly the paths the six templates read, conditional lines included;
* **complete sample**: `samples/<t>.complete.json` renders with no finding and
  shows every conditional line;
* **minimal sample**: `samples/<t>.minimal.json` leaves out every field behind a
  conditional line; the render shows none of them and leaves no label dangling;
* **overlay banner**: `pdf_watermark.assert_unwatermarked` on every complete
  render, with a positive control that stamps the invoice `Specimen` and
  requires the same check to find it.

Money is checked with `currency_marker="EUR"`: an amount on a money line that
is not preceded by `EUR` is a `RAW-AMOUNT` finding, which is how a bare
`117.0` is caught on a document whose decimal point it shares.

`account_opened` carries the one open issue, `DEMO-117` with
`finding: RAW-AMOUNT` (demo fault `doc_amount_unformatted`). With the fault on,
`judge_findings` reads it as `reproduces`; the only other finding is the pinned
charge line, which is the same defect seen twice. With the fault off it reads
`cleared` and `assert_clean()` fails with `KNOWN DEFECT CLEARED`. The example
asserts both.

**How to use it.** Add a template: one manifest entry plus
`samples/<template>.complete.json` and `samples/<template>.minimal.json`
(invented values), and declare the paths it reads in `payload.schema.json`.
The coverage and schema tests fail until all three exist.

**Configuration.** The environment the examples run as is `demo`
(`DEMO_TEMPLATE_ENV`); `absent_from_envs` values must be in
`{"demo", "legacy"}`. Amounts are read as `en_GB` digits because the demo
prints `EUR 1234.56`.

**Example.**

```json
{"template": "invoice",
 "absent_from_envs": [],
 "samples": ["samples/invoice.complete.json", "samples/invoice.minimal.json"],
 "must_show": ["Amount due:", "Net:", "Tax:"],
 "section_headings": [],
 "money_fields": ["Amount due:", "Net:", "Tax:"],
 "conditional_lines": ["Pay by:", "PO reference:"],
 "pinned_text": ["Invoice 200317", "Amount due: EUR 117.00"],
 "open_issues": []}
```

## Document content detectors

**What it does.** Two defects on the account-opened letter, each run as an expected-red
detector pair (`detector_pair.judge`, fault on and off) in
`test_document_content_detectors.py`:

| Test | Fault switch | Control | Oracle |
|---|---|---|---|
| `test_absent_contact_line_detector[fault-on/off]` | `doc_optional_prints_none` | the letter is this subscription's and has its *Details*; a letter with a contact shows the name | without a billing contact, no `Billing contact:` line is printed (`DEMO-116`) |
| `test_period_charge_format_detector[fault-on/off]` | `doc_amount_unformatted` | the invoice, from the same renderer, prints `Amount due:` as money | `Charge per period:` is printed as `EUR <amount>` and equals the period gross (`DEMO-117`) |

`test_change_notice_shows_new_values.py::test_change_notice_shows_new_values[fault-on/off]`
(`DEMO-115`, fault `document_renders_previous_version`) changes the data region and
workspace and requires the plan update notice to show the new values and `Version: 2`.

**How to use it.** Copy the shape for any "this line must (not) be printed" defect: an
oracle whose failure carries the signature, a control, and the fault-on/fault-off pair.

**Configuration.** None. The defect ids and signatures are constants in `oracles.py`
(`CONTACT_DEFECT`, `AMOUNT_DEFECT` and their signatures).

**Example.**

```python
detector_pair.judge(
    demo_control,
    ticket=oracles.AMOUNT_DEFECT,
    signature=oracles.AMOUNT_SIGNATURE,
    oracle=lambda: oracles.assert_period_charge_formatted(text, expected),
    exercised=exercised,
    fault="doc_amount_unformatted",
    fault_on=True,
)
```

## Content oracle self-tests

**What it does.** `test_document_oracles.py` pins the content oracles offline on hand-built
layouts: an absent conditional line passes and a leaked `None` fails; the absence check reads
only the *Details* section; a labelled line tells a missing line from a wrong value; a money
line tells a bare number from a wrong amount; the control rules out an empty or foreign
document; `assert_all` reports every finding, one line each; and the tracked oracles carry
their signatures only for the failure they exist for, never for a near miss.

**How to use it.** Runs with the offline suite; no demo needed.

**Example.** `pytest tests/demo_examples/documents/test_document_oracles.py`

## Order summary total matches the price response

**What it does.** Calls the price calculation, captures its response, submits the same
values, requires exactly one order summary, and requires the document's first money amount
to equal a numeric field of the captured response within 0.02 (`oracles.matching_leaf`; on
failure it names the closest field). Only currency-marked amounts count, so an id such as
`SUB-004512` is never read as an amount. The request values give the response distinct
numeric fields so the match cannot be a coincidence, and the example also pins which field
matched (`gross_amount`).

**How to use it.** `pytest tests/demo_examples/documents/test_order_summary_document.py`.

**Configuration.** Tolerance `0.02` (`TOLERANCE` in the test).

**Example.**

```python
path = oracles.matching_leaf(price_response, oracles.money_amounts(text)[0])
```

## Plan update notice rendered and downloadable

**What it does.** Applies the change in `fixtures/change-request.json` (an add-on and a new
data region), effective 20 days after the frozen demo clock, then requires one plan update
notice for the new version with a non-blank id, downloads it, checks it is a PDF and saves it
under `reports/demo_examples/documents/`.

**How to use it.** `pytest tests/demo_examples/documents/test_change_document.py -k downloadable`.

**Configuration.** Output directory `reports/demo_examples/documents/`
(relative to the working directory).

**Example.** The saved file is named `<subscription number>-plan-update-v<version>.pdf`.

## Plan update notice states the booked pro-rata

**What it does.** Books a subscription's due periods, raises the price mid-period, books the
change, and then requires that the plan update notice:

* is about the change period (`Applies from:` equals the change leg's start);
* prints an `Adjustment:` amount equal to the booked `CHANGE` ledger leg's total and to its
  invoice total, within 0.02, where the leg itself satisfies net + tax + fees = total
  (`ledger_checks.additive_identity`);
* does not print a full-period figure as the adjustment (the period gross, the period net or
  the monthly gross);
* shows the new data region in its *Details*.

**How to use it.** `pytest tests/demo_examples/documents/test_change_document.py -k pro_rata`.

**Configuration.** Tolerance `0.02`.

**Example.**

```python
adjustment = oracles.money_after_label(text, "Adjustment:")
assert abs(adjustment - Decimal(str(change_leg["total"]))) <= Decimal("0.02")
```

## New and change document content (both plan kinds)

**What it does.** `test_document_content.py::test_new_and_change_document_content` is
parametrised over the plan kinds (TEAM, INDIVIDUAL) and over two demo "environments":
`healthy` (no fault) and `faulty` (`doc_amount_unformatted` and `doc_optional_prints_none`
on). It seeds a subscription with the content fixture but no billing contact, changes it,
and reads the account-opened letter and the plan update notice (which must be a different
stored file). The control requires the subscription number, the data region inside
*Details* and the letter's own heading. The contact line and the charge format go through
`verification.fix_scope.judge`: asserted (and recorded `proved`) where the fix is expected,
run as detectors with the detector examples' signatures elsewhere; the data region and
workspace are always asserted; all findings on a document are reported together. The term
extension notice has no *Details* section and is recorded as `held_empty`.

**How to use it.** `pytest tests/demo_examples/documents/test_document_content.py`.

**Configuration.** `DEMO_DOCUMENT_FIX_ENVS` - comma-separated environments where the fix is
expected (default `healthy`). Listing `faulty` there turns the faulty runs into plain
failures, which is what a fix that should have landed and did not looks like.

**Example.**

```bash
pytest tests/demo_examples/documents/test_document_content.py -k "TEAM and faulty"
```
