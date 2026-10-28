# Oracles

The `oracles/` package holds pure functions that decide whether what a test
observed is right. They need no browser or network, so every oracle is pinned
by offline tests.

## Amount and date parsing per locale

**What it does.** Parses displayed money into `Decimal` and dates into
`date`, so tests never compare money as strings. The locale is always a
parameter. A leading minus, before or after the currency symbol, is part of
the amount: credit notes and refunds print negative amounts, and a pattern
without the minus would read a refund as a charge.

| Locale | Amounts | Dates |
|---|---|---|
| `nl_NL` (default) | `1.234,56`, `€ 1.234,56`, `-€ 12,00` | `dd-MM-yyyy` |
| `de_DE` | `1.234,56` | `dd.MM.yyyy` |
| `en_US` | `1,234.56`, `-$12.00`, `($12.00)` | `MM/dd/yyyy` |
| `en_GB` | `1,234.56` | `dd/MM/yyyy` |

```python
from oracles import amounts

amounts.parse_amount("-€ 1.380,00")  # Decimal('-1380.00')
amounts.parse_amount("($12.00)", "en_US")  # Decimal('-12.00')
amounts.amount_after_label(text, "Total due")  # first amount after the label, or None
amounts.find_amounts(text)  # every amount; dates are masked first
amounts.find_dates(text, "en_US")  # impossible dates are skipped
amounts.date_after_label(text, "Due date")
amounts.parse_json_amount(0.1)  # Decimal('0.1'), no float artefacts
amounts.format_amount(Decimal("-1234.5"))  # '-1.234,50'
```

An unknown locale raises `ValueError`. Pass a custom `NumberFormat` for any
other locale.

## PDF text

**What it does.** Extracts page text, in layout mode where supported so that a
label and its value stay on one line. It sits behind the optional `pypdf`
dependency: without it, `extract_text` raises `PdfSupportMissing` with the
install hint, and the rest of `oracles` still works.

```python
from oracles import pdf_text

text = pdf_text.extract_text("reports/invoice.pdf")  # or raw bytes
pdf_text.extract_amounts(pdf_bytes, "en_US")
pdf_text.available()  # False when pypdf is absent
```

Install it with `pip install pypdf`.

## PDF fonts

**What it does.** Lists a document's fonts and whether each is embedded, so a render after
a runtime or image change that substituted the template's font (or referenced one without
embedding it) is caught. Every page's `/Resources /Font` is read and Form XObjects are
followed (each resource dictionary once); a subset prefix (`ABCDEF+`) is removed; a font
is embedded when its descriptor has `/FontFile`, `/FontFile2` or `/FontFile3` (a `Type0`
font's descendant counts). Families compare on the part before the first `-` or `,`,
ignoring spaces and case, so `SegoeUI-Bold` is family `Segoe UI`. Same optional `pypdf`.

```python
from oracles import pdf_fonts

report = pdf_fonts.fonts(pdf_bytes)  # report.fonts, report.not_embedded, iterate Font entries
pdf_fonts.assert_fonts(pdf_bytes, "Segoe UI")  # uses the family, every font embedded
ok, why = pdf_fonts.produced_by_run(pdf_bytes, run_start, created_ids={doc_id})
```

The font check alone would pass on a stored file from an earlier render, so pair it with
the control: `produced_since(created_at, run_start, skew)` (creation or modification time
not before the run start minus clock skew, two minutes by default) or the document id being
one this run created.

## Rendered-field cleaning

**What it does.** A form field scraped from the DOM often contains its label
and any validation message as well. An empty field can then scrape as
`"Legal form Legal form has an invalid value"`. `strip_label_echo` removes
every case-insensitive, word-bounded copy of the label, plus required markers
and non-breaking spaces. Because the match is word-bounded, the value
`"Netherland"` survives a label of `"Land"`. `carries_no_value` is the check
to use: it is true when the scrape is blank, only the label, or only a
validation message.

```python
from oracles.field_text import carries_no_value, strip_label_echo

strip_label_echo("Country * Country Netherland", "Country")  # 'Netherland'
if carries_no_value(scraped, "Legal form", phrases=["is required", "invalid value"]):
    pytest.fail("the field holds no value")
```

`phrases` defaults to common English validation wording. Pass your product's
own wording in any language.

## Document integrity

**What it does.** Checks the text of a generated document for the defects
that renderers do not report. Every finding has a kind from `KINDS`:

| Kind | Meaning |
|---|---|
| `UNFILLED-SLOT` | Template syntax printed verbatim (`{{`, `<<`, `<[`, `${`, `MERGEFIELD`, …) |
| `NULL-TEXT` | `null` / `None` / `undefined` / `NaN` / `[object Object]` as a whole word |
| `DANGLING-LABEL` | A label with nothing after it on its line |
| `DANGLING-HEADING` | A block heading with no value on its line or the next 3 lines |
| `ABSENT-VALUE` | An expected value that appears nowhere |
| `RAW-AMOUNT` | An amount on a currency line using the wrong decimal separator for the locale |

```python
from oracles import document_findings as di

findings = di.all_findings(
    text,
    pinned_text={"customer": "Example Trading Ltd"},
    must_show=["Invoice number", "Total due"],
    section_headings=["Delivery address"],
    money_fields=["Total due"],
    locale="en_GB",
)
assert not findings, di.describe("invoice", findings)
```

Use `all_findings` whenever a verdict depends on a finding being *absent*. An
oracle that never ran cannot report anything, so a "fixed" verdict reached
without it means nothing.

## Template placeholder scan

`oracles/placeholder_scan.py` reads a template's *source* and checks its placeholders
before anything is rendered. Lenient engines print an empty string for a path the payload
does not have, and copy a placeholder with a damaged bracket into the output as literal
text. Neither fails a render, and a sample payload only exercises the fields it fills, so
the scan looks at every placeholder instead.

Two dialects, picked with `syntax`:

- `mustache` (default): `{{ buyer.city }}`, `{{ amount|money }}`,
  `{{#if paid}}…{{else}}…{{/if}}`, `{{#each lines}}…{{/each}}`.
- `mergefield`: Word field codes, `MERGEFIELD payer_name \* MERGEFORMAT`. No else, no loops,
  no filters.

Any other name raises `ValueError` listing the two.

```python
from oracles import placeholder_scan as ps

source = ps.package_source_text(
    docx_bytes
)  # body, headers, footers; markup stripped so split runs rejoin
ps.placeholder_paths(source)  # ['buyer.city', 'amount', ...] in order, repeats kept
ps.damaged_placeholders(source)  # e.g. '{ buyer.city }}' -- would print verbatim
ps.conditional_blocks(
    source
)  # [Conditional(guard, has_alternative)], an else counts only for its own block
ps.value_filters(
    source
)  # ValueFilter(text, left_quote, right_quote); .uses_typographic_quote, .quotes_disagree
ps.top_level_names(source)  # first path segments read, never this/true/false
ps.undeclared_paths(source, schema, loop_aliases={"line": "order.lines"})
```

`schema_declares(schema, path)` walks `properties`, steps through arrays to their `items`,
and lets an object with `additionalProperties: true` (or a schema) accept any child.
`is_zip_package` / `is_compound_file` tell a `.docx` from a legacy binary `.doc`;
`package_source_text` raises `ValueError` ("unreadable") on the latter, so a caller reports
the file as unscanned rather than clean.

## Structural validation and rendered checks for a template estate

```python
from oracles import template_structure as ts

cov = ts.manifest_coverage(
    deployed_names, manifest_names, quarantine=["legacy-letter"], exempt_prefixes=["ledger-"]
)
assert cov.ok, cov  # undescribed, stale quarantine entries, duplicate names

findings = ts.validate_template(
    "invoice",
    docx_bytes,
    syntax="mustache",
    schema=payload_schema,
    known_formats={"dd-MM-yyyy", "0.00"},
)
# kinds: UNREADABLE, BAD-DELIMITER, CURLY-QUOTE, UNKNOWN-FILTER, UNDECLARED-PATH

results = ts.check_rendered(entries, render=my_render, extract=pdf_text.extract_text)
assert all(not f for f in results.values()), results
```

Parametrize over the templates so each broken template gets its own red
result, rather than one for the whole suite.

## Day counts and refund bounds

**What it does.** Computes day counts under the common conventions, and
gives a refund assertion that does not depend on which convention the system
uses.

| Convention | Rule |
|---|---|
| `ACT_365F`, `ACT_360` | Actual days / 365 or 360 (`year_fraction`) |
| `ACT_ACT` | Actual days over the period's actual length |
| `THIRTY_360_ISDA` | 30/360 bond basis: D2 is capped at 30 only when D1 is already 30 |
| `THIRTY_360_US` | Bond basis plus the end-of-February rules |
| `THIRTY_E_360` | D1 and D2 are always capped at 30 |
| `THIRTY_E_360_ISDA` | Any month-end counts as 30 (except a February maturity) |

30/360 ISDA and 30E/360 differ **only when the later date is the 31st**. For
example, 15 Jan → 31 Mar is 76 days under ISDA and 75 under 30E/360. A test
pinned to one convention, run against a system that uses the other, goes red
only on runs whose dates fall on a 31st. Include the 31st in your cases.

```python
from datetime import date
from decimal import Decimal
from oracles import daycount as dc

p = dc.Period(date(2030, 3, 15), date(2030, 4, 14), termination=date(2030, 3, 31))
dc.candidate_refunds(p, Decimal("90.00"))  # one figure per convention, negative
dc.describe_match(
    p, Decimal("90.00"), observed
)  # never treats a tie as evidence for one convention

dc.assert_pro_rata_refund(
    "order A-1", p, Decimal("90.00"), observed, refund_tax=observed_tax, tax_rate=Decimal("0.20")
)
```

The bounds assertion checks four things:
- the refund is negative;
- it is strictly smaller than the full period (zero fails);
- it is taxed at `tax_rate` (only when a rate is given);
- the refunded fraction matches the remaining days under *some* modelled
  convention, which catches a refund computed over the wrong period.

`Period.end` is inclusive by default (`end_inclusive=True`).

## Mid-period date chooser

```python
from oracles.period_window import Window, choose_mid_period_date, last_booked_period

period = last_booked_period(rows)  # [(start, end, state)], state "BOOKED" by default
chosen = choose_mid_period_date(
    period, days_into=45, blocked=scheduled_windows, valid_to=end_of_validity
)
if not chosen:
    pytest.skip(chosen.reason)  # NoWindow says which rule ruled it out
```

The chosen date is strictly inside the period. If `days_into` falls outside
it, the midpoint is used instead.

## Charges and change deltas

```python
from oracles.charges import ChargeSchedule, sign_matches

schedule = ChargeSchedule(
    {1: Decimal("0.05"), 3: Decimal("0.03"), 12: Decimal("0")}, tax_rate=Decimal("0.20")
)
schedule.compute(Decimal("10.10"), 1)  # net/tax/total, half-up to cents
schedule.installment_delta(old, new, 3)  # exact
schedule.period_delta_ceiling(old, new, 3)  # an upper bound, not an expected value
```

## Ledger schedule checks

`oracles.ledger_checks` provides `additive_identity`, `contiguous` (gaps and
overlaps), `steps_match_term`, `allowed_values` and `sequence`. Each returns a
list of problems, empty when the check holds.

## Recompute oracle

**What it does.** Checks that derived amounts follow a changed input, using
an observed baseline. Only paths that were non-zero in the baseline are
compared. A path missing after the change counts as recomputed. An empty
baseline means the precondition was not met: it is not a pass.

```python
from oracles.recompute import RecomputeCheck

check = RecomputeCheck.of(baseline_amounts, amounts_after_zeroing)
if check.baseline_is_empty:
    pytest.fail("precondition: no non-zero amount observed before the change")
assert check.recomputed, check.describe()
```

## Document-vs-ledger reconciliation

```python
from oracles.reconciliation import reconcile

result = reconcile(
    document_text,
    ledger_legs,
    {"Net amount": "net", "Tax": "tax", "Total": "total"},
    locale="en_GB",
    compare_magnitudes=True,
    expected_sign=-1,
)
assert result.ok, result.describe()
```

Mismatch kinds are `missing-on-document`, `missing-in-ledger`, `amount`
(outside `tolerance`, default 0.01) and `sign`.

## Rejection classifier

**What it does.** Decides whether an operation was refused, on any of six
surfaces: `API_STATUS` (4xx), `FIELD_MESSAGE`, `CONTROL_FLAGGED`,
`BANNER_MESSAGE`, `VALUE_ADJUSTED` and `STATE_UNCHANGED`. Two cases are
deliberately *not* treated as rejections:
- a correction where the entered value was already in range;
- a blank or unparseable read-back, which is also what a broken locator
  produces.

```python
from oracles.rejection import Bound, RejectionSignals, appeared_since

signals = RejectionSignals(
    entered="150",
    allowed=Bound.at_most("100"),
    read_back=field_value,
    inline_errors=appeared_since(errors_before, errors_after),
    page_alerts=appeared_since(alerts_before, alerts_after),
    control_invalid=aria_invalid,
    api_status=last_status,
    state_changed=moved,
)
assert signals.entered_is_out_of_bound()  # precondition
assert signals.rejected, signals.describe()
```

`appeared_since` compares message lists as multisets, so messages that were
already on screen do not count as signals.

## Data health

**What it does.** Scans read-only API listings for records that will break a
later operation. The scanner can only send `GET` (`GetOnlyApi`). A scan cut
short by a page cap, or by a refused read, is reported as incomplete; it never
counts as clean.

```python
from datetime import timedelta
from oracles import data_health as dh

rules = [
    dh.datetime_precision(["start", "end"], fraction_digits=3),
    dh.date_without_time(["start", "end"]),
    dh.status_in("status", {"FAILED"}, "failed-status"),
    dh.required_when("kind", "REFUND", ["payee.account"], "refund-payee"),
    dh.stale_by_uuid7("draft_id", max_age=timedelta(days=2)),
]
scanner = dh.Scanner(
    dh.GetOnlyApi.over(api),
    dh.select_rules(rules, "all"),
    items_path="items",
    page_size=100,
    max_pages=10,
)
report = scanner.scan("records")
assert report.complete and not report.findings, report.describe()
```

`select_rules` takes `all` or a comma-separated list of rule ids. A custom rule
is `Rule(id, title, check)`, where `check(record)` returns a list of messages.

## Post-deploy smoke

**What it does.** Runs a few read-only `GET` checks that must answer, and
answer in the right shape. A transport failure is a failure, never a skip.

```python
from oracles import data_health as dh, smoke

checks = [
    smoke.SmokeCheck(
        "version", "version", (smoke.status_is(200), smoke.json_path_non_blank("version"))
    ),
    smoke.SmokeCheck("listing", "items", (smoke.json_path_is_list("items"),), params={"size": 1}),
]
results = smoke.run_smoke(dh.GetOnlyApi.over(api), checks)
assert all(r.ok for r in results), smoke.describe(results)
```

**Anonymous release smoke behind auth.** Two more expectations prove the
service itself answered without holding a token. `api_document(title, paths,
security_scheme=None)` requires a 200 JSON API document carrying the
service's own `info.title`, a GET operation on every path in `paths` and,
when given, the security scheme. `auth_challenge(scheme="Bearer")` requires
401 with a `WWW-Authenticate` challenge for that scheme, so the refusal came
from the service's security chain rather than from a proxy in front of it.

```python
from oracles import smoke

checks = [
    smoke.SmokeCheck("doc", "/v3/api-docs", (smoke.api_document("Plans", ["/plans"], "bearer"),)),
    smoke.SmokeCheck("challenge", "/plans", (smoke.auth_challenge(),)),
]
results = smoke.run_smoke(GetOnlyApi.over(anonymous_engine), checks)
```

## Validation reporting

`with reporting.validating("refund is negative", expected="< 0"):` wraps an
assertion in a named Allure step and logs `VALIDATING` / `OK` / `FAILED`.
`reporting.skipped(intent, reason)` records a check that was deliberately not
run, so its absence is visible in the report. `reporting.note(name, body)`
attaches a diagnostic.

## Scenario manifest

Declarative scenario rows that become parametrized tests are documented with
the test-data tools; see `test-data.md`.

## Rendered PDF inspection

**What it does.** `PdfTextView.parse(data)` (`oracles/pdf_text_view.py`) reads
downloaded bytes once and returns the size, producer, creator, page count,
layout text and plain text. Bytes that are not a PDF (an HTML error page, a
JSON error) raise `NotAPdf` with the first bytes in the message, so they are
never judged as an empty document.

`watermark_findings()` reports the configured overlay phrases (evaluation
banners, "specimen", "draft copy") that appear in the document. Matching
ignores case and **all** whitespace, because overlays are often drawn glyph
by glyph or diagonally across the body, which splits the words on
extraction. Layout text and plain text are both searched. `produced_by(name)`
checks the producer/creator metadata, so output from a fallback engine is
caught.

```python
from oracles.pdf_text_view import PdfTextView

doc = PdfTextView.parse(api.get_bytes("documents/42/render", accept="application/pdf"))
assert doc.page_count >= 1 and doc.produced_by("Example Renderer"), doc.describe()
assert doc.watermark_findings() == [], doc.describe()
```

**Configuration.**

| Env var | Default | Meaning |
|---|---|---|
| `PDF_OVERLAY_PHRASES` | `evaluation only, evaluation copy, evaluation version, created with an evaluation, not for production use, specimen, draft copy` | Comma-separated phrases to report; a per-call `phrases` argument wins |

Needs `pypdf`, like [PDF text](#pdf-text).

## Overlay-banner assertion

**What it does.** `assert_unwatermarked(source)` (`oracles/pdf_watermark.py`)
turns the inspection above into an assertion with both sides covered. A
clean document passes and is returned parsed. A stamped one raises
`OverlayFound`, naming every phrase found and the producer. Non-PDF bytes
raise `NotAPdf` instead of reading as "no banner found". With
`expected_producer=` it also fails when another renderer produced the file.
`find_overlay_phrases(source, phrases)` returns the matches without
asserting. Both accept raw bytes or an already parsed `PdfTextView`.

```python
from oracles.pdf_watermark import assert_unwatermarked

doc = assert_unwatermarked(pdf_bytes, expected_producer="Example Renderer")
```

Configuration is the same `PDF_OVERLAY_PHRASES` / `phrases` argument as above.

## PDF extraction contract

**What it does.** `tests/oracles/test_pdf_extraction_contract.py` pins exactly
what `pdf_text.extract_text` makes of a known two-page document: every line,
the spacing between label and value, block order, and that no document
oracle fires on it. A `pypdf` upgrade that changes layout heuristics changes
every document verdict at once. With this test it goes red offline first.

The fixture `tests/oracles/fixtures/rendered-invoice.pdf` has invented content
and is written by a stdlib-only PDF writer (`tests/oracles/pdf_fixtures.py`),
byte for byte reproducibly. A test checks the committed file still matches the
writer.

**How to use it.** After a `pypdf` bump that turns it red, regenerate the
fixture only if the writer changed, then re-derive `EXPECTED_LINES` from the
new output. Check that label/value pairs still share a line before you accept
it. Do not loosen the assertions.

```bash
python -m tests.oracles.pdf_fixtures          # rewrite the fixture
pytest tests/oracles/test_pdf_extraction_contract.py
```

The test module skips cleanly when `pypdf` is absent.

## Activity-export archive reader

**What it does.** `read_activity_export(zip_bytes)` (`oracles/activity_export.py`)
reads an activity-log export: a zip that holds a CSV. It returns
`CallRecord(actor, method, path, status)` records, with the raw row kept on
`row`. The CSV is read per RFC 4180 (quoted fields, doubled quotes, newlines
inside a field, UTF-8 BOM). The entry is found by exact name, else by file
name ignoring case and directories. A missing entry fails with the archive's
listing.

Some exporters render a structured column as a `{key=value, ...}` map.
`parse_map_cell` tokenises such a cell instead of splitting it on commas. A
comma only separates entries when `key=` follows it, nested `{}`/`[]` stay
whole, and double-quoted values may hold anything. So
`path=/api/items?ids=4,5` keeps its full value.

```python
from oracles.activity_export import ColumnMap, read_activity_export

columns = ColumnMap(
    actor=("who", "mail"), method=("request", "verb"), path=("request", "url"), status="status"
)
rows = read_activity_export(
    api.get_bytes("audit/exports/7/download"), entry="activity.csv", columns=columns
)
assert any(r.method == "DELETE" and r.path == "/api/items/9" for r in rows)
```

**Configuration.** `entry` (default `call_log.csv`) and `ColumnMap`. Each
field is a column name, or `(map column, key)` for a key inside a map cell.
Defaults: `actor`, `method`, `path`, `status`. The method is upper-cased and
the status parsed as an integer; a non-numeric status raises
`ExportFormatError`.

## Expected-code mapping for migrations

**What it does.** `CodeMapping` (`oracles/code_mapping.py`) tells migrated
records from unmigrated ones when a migration re-codes terms by description.
The table (`description -> code`) is loaded from a JSON file, never copied
into test code: a hand-kept copy drifts from what the migration really uses.
Descriptions match after trimming, collapsing whitespace and case folding.
Conflicting duplicates and non-text entries are rejected at load time.

`mapped_terms(entity, terms)` lists, per term the table knows, the actual
and expected code (`CodeMatch.converted`). `state(mapped)` sums that up as
`MIGRATED`, `UNMIGRATED`, `PARTIAL` or `NOT_APPLICABLE`.

```python
from oracles.code_mapping import CodeMapping, MigrationState

mapping = CodeMapping.load("data/plan-codes.json")  # {"Standard cover": "PLN-110", ...}
terms = mapping.mapped_terms(
    "SUB-000123", entity["plan_terms"], description_key="description", code_key="code"
)
assert mapping.state(terms) is MigrationState.MIGRATED, "\n".join(map(str, terms))
```

**Configuration.** The JSON file path, and the `description_key` / `code_key`
of the term objects (defaults `description` / `code`).

## Business-number sequences

**What it does.** `oracles/id_sequences.py` checks the numbers a system
issues (`SUB-000123`, `ORD-EU-004211`):

- `parse` checks the shape: prefix, optional segment, zero-padded counter. A
  counter shorter than the padding is rejected, and so is one longer than it
  that still has a leading zero.
- `strict_increase_violations` checks that numbers rise strictly in issue
  order, per series (prefix + segment). Gaps are allowed; a repeat or a step
  back is not.
- `shared_numbers` finds a number held by more than one entity.
- `unstable_entities` finds an entity whose versions carry more than one
  number, which means a "draw once" guard let a re-entry draw again.

`check_sequence` runs them all over entities listed in creation order.

```python
from oracles.id_sequences import Issued, check_sequence

items = [Issued(s["id"], [v["number"] for v in s["versions"]]) for s in created_in_order]
report = check_sequence(items, expected_prefix="SUB")
assert report.ok, "\n".join(report.problems())
```

**Configuration.** `pattern` is a regex with named groups `prefix`, `counter`
and optionally `segment`. `padded_length` defaults to 6, and
`expected_prefix` is optional.

### Hand-over from migrated numbering

`handover_violations(numbers, origin_of, legacy, new)` names every number of
the `new` origin at or below the highest `legacy` number (counters compared
with `parse_counter`, so padding does not decide the order). It is only
meaningful over the complete list: `require_complete(read, total)` raises
`IncompleteRead` when the read came back short of the reported total. With no
numbers of either origin the check abstains (`no_verdict`): it would be vacuous.

```python
from oracles.id_sequences import handover_violations, require_complete

require_complete(len(rows), page.total)
numbers = {r["number"]: r["origin"] for r in rows}
assert handover_violations(numbers, numbers.get, "migrated", "native") == []
```

## Job-registration classifier

**What it does.** `oracles/job_registration.py` reads how a schedule's rows
were registered with a job scheduler, so a test can tell which build
produced it. `classify_registration` returns one of:

| Rule | Picture |
|---|---|
| `SINGLE_SCHEDULED` | Exactly one row scheduled, at least one pending |
| `EVERY_ROW_SCHEDULED` | Two or more scheduled, none pending |
| `NOTHING_SCHEDULED` | Unbooked rows, none scheduled |
| `MIXED` | Several scheduled next to pending ones; neither rule makes this |
| `UNDETERMINED` | Fewer than two unbooked rows; both rules look alike |

- `one_at_a_time_violations` lists breaches of that rule: not exactly one
  scheduled row, the scheduled row is not the first pending one, or it
  has no job id.
- `silently_stalled` is true when a row was booked, rows still wait, and none
  is registered: the chain stopped without an error.
- `expected_run_time(period_start, registered_at)` gives when the job should
  fire: the period start, or the registration plus the minimum delay when
  that is later.

```python
from oracles import job_registration as jr

rows = jr.parse_rows(api.get(f"subscriptions/{sid}/invoices").json()["items"])
assert jr.classify_registration(rows) is jr.Rule.SINGLE_SCHEDULED
assert not jr.one_at_a_time_violations(rows) and not jr.silently_stalled(rows)
```

**Configuration.** `StatusVocabulary(scheduled, pending, booked)` (defaults
`{"scheduled"}`, `{"pending"}`, `{"booked", "paid"}`). `parse_rows` key
names default to `id`, `code`, `status`, `start`, `job_id`, and
`min_delay` defaults to 5 seconds. A start time without an offset reads as
unreadable, never as local time.

## Bank statement (camt.053) parser

**What it does.** `parse_camt053(xml)` (`oracles/bank_statement.py`) turns an
ISO 20022 camt.053 statement into `StatementEntry` records with these fields:

- amount as `Decimal`, and currency;
- direction (`CRDT` in, `DBIT` out) and `signed_amount`;
- booking date and end-to-end reference;
- counterparty: the creditor on a debit, the debtor on a credit. Both the
  `Cdtr/Nm` and `Cdtr/Pty/Nm` layouts are read;
- unstructured remittance lines, joined.

Elements are matched by local name, so every camt.053 version and prefix
style parses the same. A document with a DOCTYPE is refused. Malformed
entries raise `StatementFormatError`.

```python
from decimal import Decimal
from oracles.bank_statement import parse_camt053

entries = parse_camt053(api.get("payments/statement").text)
paid = {e.reference: e.amount for e in entries if e.direction == "CRDT"}
assert paid.get("INV-2031-0042") == Decimal("125.40")
```

No configuration.

## EDIFACT interchange parser

**What it does.** `parse_interchange(text)` (`oracles/edifact.py`) parses a
UN/EDIFACT interchange. It honours a `UNA` service string (else the default
characters `:+.? '`) and the release character (`?+` is a literal plus). It
splits every element into its components, ignores line breaks between
segments, and groups segments into `Message`s on `UNH`..`UNT`.

- `Segment.element(i)` and `Segment.component(i, j)` read values.
- `Interchange.find_message(predicate)` selects a message.
- `group_by(segments, start_tag)` builds entity groups for formats that model
  an entity as a start segment and its followers.
- `Interchange.completeness()` checks the envelope counts a receiver checks:
  - each `UNT` counts its message's segments and repeats the `UNH` reference;
  - `UNZ` counts the messages and repeats the `UNB` reference.

```python
from oracles.edifact import group_by, parse_interchange

ic = parse_interchange(outbound_file_text)
assert ic.completeness() == []
msg = ic.find_message(lambda m: m.first("BGM").element(1) == "ORD-42")
lines = group_by(msg.segments, "ITM")
```

No configuration; industry-specific segment vocabularies are left to the
caller.

## UUID version and time-ordered ids

**What it does.** `oracles/ids.py` reads what an id says about its generator:

- `uuid_version` gives the version (4 random, 7 time-ordered, ...).
- `uuid7_timestamp` gives the UTC instant embedded in a v7 id.
- `consistent_with_generation_time(id, generated_at, tolerance)` checks that
  instant against the generation time a record claims.
- `time_order_violations(ids)` finds v7 ids whose time steps back in creation
  order.

When a service switches generators, the version tells which build wrote a
record. A naive datetime raises `ValueError`.

```python
from oracles.ids import consistent_with_generation_time, uuid_version
from oracles.time_window import parse_instant

assert uuid_version(message["id"]) == 7
assert consistent_with_generation_time(message["id"], parse_instant(message["created_at"]))
```

**Configuration.** `tolerance` defaults to 5 seconds.

## Produced-since time window

**What it does.** `produced_since(records, record_type, since)`
(`oracles/time_window.py`) keeps the records of one type whose timestamp is on
or after a moment such as a deploy, so a check about the new build never
judges output the old one wrote. Mixed offsets (`+02:00`, `Z`) are compared as
instants. A timestamp without an offset, or a missing or unreadable one on a
record of the wanted type, raises `TimestampError`. Guessing a timezone would
silently move records across the cut-off, and dropping them would hide the
record a test is looking for. A naive cut-off is refused too.

```python
from oracles.time_window import parse_instant, produced_since

deployed = parse_instant(os.environ["DEPLOYED_AT"])  # e.g. 2031-05-04T10:00:00Z
new_files = produced_since(
    details["files"], "statement", deployed, type_key="type", time_key="created_at"
)
```

**Configuration.** `type_key` / `time_key` (defaults `type` / `created_at`);
`record_type=None` keeps every type.

## Audit-trail release check

**What it does.** `oracles/audit_trail.py` runs the audit part of a release
check. `export_window` requests an export for the window since the deploy,
polls the export list until the export reaches a terminal status (`COMPLETED`,
`FAILED`, `CANCELLED` by default), then downloads and parses it
(`read_export_rows`: every `.json` and `.csv` entry in the zip). A failed export
raises `ExportFailed`, a slow one `ExportTimeout`.

The control comes next: `require_own_activity(rows, app)` requires rows the
service produced itself, so an empty match cannot be read as "nothing was
audited". `events` matches structured category and event codes exactly,
`check_events` checks the actor of each match and runs `secret_like` over every
row (the same name rule as `contract_testing/redaction.py`). With no matching
event in the window it abstains.

```python
from oracles import audit_trail as at

rows = at.export_window(engine, deployed_at, now, create=create, list=listing, download=fetch)
at.require_own_activity(rows, "plans-service")
assert at.check_events(rows, category="PLAN", event="UPDATED", actor="release-bot") == []
```

## Served schema declares the required inputs

**What it does.** `oracles/schema_required.py` reads the schema a service
validates against and returns the field names that must be required but are
not: `missing_required(document, locate, required)`. `locate` is a node name
(searched recursively, exactly one match) or a callable. When none of the
expected names is declared under `properties`, it raises `NotExpanded`
instead of reporting them: an unexpanded schema cannot say "not required".
`served_missing_required` does the GET through a `GetOnlyApi`.

```python
from oracles.schema_required import served_missing_required

assert served_missing_required(api, "/api/machines/subscription", "cancelInput", ["reason"]) == []
```
