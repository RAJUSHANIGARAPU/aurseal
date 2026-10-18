# Demo examples: outbound messaging

Worked examples of outbound partner messages against the in-repo demo service
(`demo_sut`, see [demo-sut.md](demo-sut.md)). Examples are in
`tests/demo_examples/messaging/`; the reusable pieces are in `oracles/` and
`api/clients/`. Everything runs offline:

```bash
pytest tests/demo_examples/messaging tests/oracles/test_message_*.py \
       tests/oracles/test_send_order.py tests/api_layer/test_outbound_messages_client.py
```

How the demo handles messages: booking the first transaction of a create, change, renewal
or cancellation hands one message to the outbox with status `NEW`. *Generate* stores two
files per message, a segment payload and an XML document-data file, and moves the message
to `TO_BE_SENT`. *Send* delivers the files in the configured order, and the message ends
`SENT` or `FAILED`. Nothing leaves the process. Every example freezes the demo clock at
`2026-03-10T09:00:00Z`.

## Outbound messages client

**What it does.** `api/clients/outbound_messages_client.py::OutboundMessagesClient` is a
client over `HttpEngine` for reading outbound messages:

| Method | What |
|---|---|
| `overview(subject_id=None, since=None)` | messages, newest first. The client sorts them itself, so callers never depend on server order. |
| `details(id)` / `wait_for_files(id)` | one message. `wait_for_files` polls until the message lists files and raises `StillWaiting`, with the last state seen, on timeout. |
| `download(id, fmt=None)` | a generated file's bytes. Raises `NotGenerated` while there are none (404), and `ApiError` for any other failure. |
| `generate(subject_id=None)` | the one write: turns `NEW` messages into stored files and polls the background job until it ends. It sends nothing. |

There is deliberately no `send` or `resend`. Code that holds this client cannot deliver
anything to a partner.

**How to use it.**

```python
outbound = OutboundMessagesClient(demo_http, timeout_s=10, poll_s=0.05)
(message,) = outbound.overview(subscription_id)
outbound.generate(subscription_id)
segments = outbound.download(message["id"])
xml = outbound.download(message["id"], fmt="xml")
```

**Configuration.** Constructor arguments: `timeout_s` (default 10), `poll_s` (default
0.1), `paths=Paths(...)` for a service with another URL layout, and `sleep` / `clock`
(injectable for offline tests).

**Demo example.** `test_outbound_generate.py`: overview, `NotGenerated` while the message is
`NEW`, generate, files `READY` and unsent, download of both parts, newest first.

## Payload completeness rules

**What it does.** `oracles/message_rules.py` is a generic rule runner. Each `Rule` checks
one property of a parsed message and carries its own `reason`, so a failure reads as a
finding. `evaluate(subject, rules)` runs them all. A rule that crashes is reported as a
problem, never skipped. `violations(results)` gives one line per problem:
`rule: problem (reason)`. Factories:

* `totals_balance(name, total, parts)`: the total equals the sum of its parts. A missing or
  non-numeric part is reported, never counted as zero.
* `required_group(name, tag, at_least=1)`
* `code_mapped(name, value, source, table)`: the message carries `table[source]` for a known
  input.
* `declared_count(name, trailer)`: the trailer's declared segment count matches.
* `xml_well_formed(name, extract, allow_declaration=True)`

`segment_value(tag, element, qualifier=None)` reads values from segments parsed by
`oracles.edifact.parse_interchange`.

**How to use it.**

```python
from oracles import message_rules as mr

amt = lambda q: mr.segment_value("AMT", 1, qualifier=q)
rules = [mr.totals_balance("invoice total", amt("TOT"), [amt("NET"), amt("TAX")],
                           reason="the receiver rejects an unbalanced total")]
assert mr.violations(mr.evaluate(segments, rules)) == []
```

**Configuration.** `totals_balance(..., tolerance=Decimal("0.00"))`.

**Demo example.** `test_outbound_payload_rules.py` builds the demo's rule set
(`messaging/rules.py`) and runs it twice. Real payloads for an individual and a company
customer hold every rule. A tampered payload (customer group removed, XML cut off) is caught.

## Stored-once rule

**What it does.** `oracles/message_ledger.py::stored_once(before, after,
handoff_succeeded=, expected_type=)` compares one subject's overview read just before and
just after a hand-off. The rule holds when:

* every earlier message keeps its id, and no id appears twice;
* after a successful hand-off, exactly one message is new, with the expected type, a fresh
  status (`NEW` / `TO_BE_SENT`), the same recipient as before and a creation time that does
  not go back;
* after a failed hand-off, nothing new appears.

**How to use it.**

```python
before = outbound.overview(sub_id)
...  # the hand-off
assert stored_once(before, outbound.overview(sub_id),
                   handoff_succeeded=True, expected_type="CHANGE") == []
```

**Configuration.** Keyword arguments `recipient`, `fresh_statuses` and the key names
(`id_key`, `type_key`, `status_key`, `recipient_key`, `created_key`).

**Demo example.** `test_outbound.py::test_change_stores_exactly_one_message` reads the
overview before and after a change: the control is the booking's one complete message, and
the change must add exactly one more, which is complete too.

## Send order: which build sent this?

**What it does.** `oracles/send_order.py` declares file orderings as `Hypothesis` objects:
`BY_TYPE`, `TYPE_THEN_NAME`, `TYPE_THEN_ID` (numeric ids compare as numbers).
`violations(files, hypothesis)` checks two rules:

* **prefix**: no file is sent while a file ordered before it is still waiting;
* **stamps**: by their send stamps, the files follow the declared order.

Every violation quotes every file's values. `fitting_hypotheses` lists the orderings a
message fits, and `identify(files, builds)` returns the one build whose ordering fits, or
`None`. A message with fewer than two sent files is not `orderable`. A single-file resend is
an explicit exception and never counts as evidence.

**How to use it.**

```python
files = files_from(outbound.details(message_id))
assert identify(files, {"v1": TYPE_THEN_ID, "v2": TYPE_THEN_NAME}) == "v1"
```

**Configuration.** `files_from(details, type=..., id=..., ...)` maps other key names.

**Demo example.** `test_send_order_identifies_build.py` runs once per demo `send_order`
setting (v1, v2). It adds two attachments whose id order and name order disagree, lets the
demo's own sender deliver, and identifies the setting from the observed sequence. Without
such a tie, one file per type, the message identifies nothing, and the example asserts that
too.

**Demo service addition.** `POST /api/messages/{id}/attachments` with
`{"name": ..., "content": ...}` adds a file of type `ATTACHMENT` to a generated
(`TO_BE_SENT`) message. It answers 409 before generation, 404 for an unknown message and
422 without a name. It lives in `demo_sut/message_attachments.py` and is purely additive.

## Invoice notes carried in the document data

**What it does.** The XML document data of an outbound message carries the subscription's
invoice notes under `invoiceNotes/note`. `test_notes_carried.py::test_invoice_notes_round_trip_in_xml`
is a detector pair (`DEMO-109`) on the demo fault `xml_escapes_twice`: a note with
characters XML must escape (`R&D <pilot>`) has to read back exactly as stored. The control
sends a note with nothing to escape, which round-trips either way, and checks the XML
carries exactly one note.

## Outbound end to end

**What it does.** Examples in `test_outbound.py`:

* `test_change_stores_exactly_one_message`: the control is one complete booking message. A
  change effective today then adds exactly one more message (stored-once), and that one is
  complete too.
* `test_outbound_segment_message_complete`: generates (again, while the message is still
  `NEW`), downloads, parses the segments, finds the one `NTE` note segment and runs the
  completeness rules.
* `test_booked_and_changed_subscriptions_reach_outbox`: team and individual subscriptions
  are listed within a bounded number of reads. A missing file is accepted only while the
  message is `NEW`. A change produces a message too.
* `test_non_ascii_name_is_sent_intact`: a customer named `Zoë Ångström` gets a booked
  subscription; the message ends `SENT` and carries the name byte for byte in UTF-8.
  `test_encoding_guard_bites_when_the_defect_is_back[fault-on/off]` (`DEMO-108`) shows the
  same oracle failing with the demo fault `message_send_fails_on_non_ascii` on; the control
  sends an ASCII name.

## Message formats and ids

**What it does.** Examples in `test_message_formats.py`:

* `test_xml_messages_still_generated`: after a runtime change (the demo setting
  `deployed_at`), messages created since that instant download as well-formed XML. A message
  from before it is not in the list, and a new booking produces one.
* `test_send_order_and_time_ordered_ids`: a read-only inspection of messages created since a
  cut-over. Their files fit the declared order (type, then id), every id is a UUIDv7, and the
  ids are time-ordered (`oracles/ids.py`). The reads go through `messaging/readonly_reader.py`
  over `gate.read_only.ReadOnlyClient`, and `find_write_calls` proves that module contains
  no write call.

**Configuration.** Demo settings `deployed_at`, `send_order` (v1 / v2) and
`message_id_scheme` (uuid7 / uuid4).
