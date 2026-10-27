# demo_sut — an invented team-software subscription service

A small, self-contained system under test for the example suites. Customers subscribe
to team software plans through resellers (*partners*); the service bills each
subscription per period, writes PDF documents, hands messages to an outbox and has a
few admin screens. Everything in it is invented: names, numbers, rules and wording.

* **Standard library only**: WSGI (`wsgiref`), `sqlite3`, `json`. PyYAML (already a
  project dependency) reads the lifecycle file.
* **One SQLite file per instance**, so each test session (each xdist worker) has its
  own data. A read-only probe can open it with `file:<path>?mode=ro`.
* **A settable clock**: every date the service writes comes from it.
* **Fault switches**: invented defects, off by default and **always reported**.
* **A committed OpenAPI document** (`openapi.json`) that a test keeps in sync.

```bash
python -m demo_sut --port 8765            # fresh temporary database
python -m demo_sut --port 8765 --db demo.sqlite
python -m demo_sut openapi --write        # regenerate demo_sut/openapi.json
```

## Resource model

| Resource | Key fields | Notes |
|---|---|---|
| customer | `number` (`CUS-D1-n`), `type` INDIVIDUAL/COMPANY, `display_name` (up to 64 characters), `email`, `phone` (digits, spaces, leading `+`), `billing_address` {`line1`, `line2`, `postal_code`, `city`, `country`}, `payout_account`, `status` pending_verification/verified | rules below |
| reseller terms | `name`, `share_percent` (0 to the cap), `flat_fee` | one seeded: *Standard terms*, 10 % |
| partner (reseller) | `number` (`PAR-D1-n`), `reseller_terms_id` | one seeded: *Seed Channel Partner* |
| plan | `code`, `kind` TEAM/INDIVIDUAL, `base_monthly_net`, `tax_rate`, `setup_fee` | two seeded: TEAM 32.50 + setup 20.00, INDIVIDUAL 9.75 + setup 4.00, tax 20 % |
| subscription | `number` (`SUB-000123`), `status`, `version`, `holder` (read from the current customer record), `partner`, `price`, `currency` (EUR, fixed), `spend_cap`, `addons` {`sso`, `audit_trail`, `sandbox`}, `data_region`, `workspace_type`, `invoice_notes[]`, `labels[]`, `seat_blocks[]` + `seat_total`, `billing_contact`, `payment_method`, `po_reference`, `invoice_delivery`, `fees`, `term_start/term_end` | lifecycle below |
| version | `version`, `effective_from`, `cause`, full snapshot | `current` = the latest version already in effect |
| draft | `kind` change/cancel/renew/note, `status` OPEN/CONFIRMED/DISCARDED, `effective_date`, `changes`, `steps`, `preview` | one open change/cancel/renew draft per subscription |
| transaction | `kind` NEW/CHANGE/RENEW/CANCEL, `state` INITIAL/SCHEDULED/BOOKED/VOID, `period_start/end`, `net`, `tax`, `fee_net`, `fee_tax`, `total`, `job_id`, `reference` (`TX-n`) | one per billing period |
| invoice | numeric `number`, `status` OPEN/PAID, `net/tax/total/remaining`, booking/document/reference/due/collection dates, `document_id` | issued when a transaction is booked |
| journal entry | `references[]`, `lines[]` `{account, role, debit, credit}` | always balanced |
| ledger account | `number`, `name`, `managed`, `role` RECEIVABLE/REVENUE/TAX/FEES | managed accounts cannot be renamed |
| settlement | reseller earnings over booked periods, `closing_balance`, `reference` (`SET-00001`), `status` open/closed | statement export = one signed entry |
| job | `kind` BOOK_TRANSACTION / GENERATE_MESSAGES, `state` | booking jobs per transaction; generation runs in the background |
| document / file | `template`, `file_id`; files hold the bytes | PDFs written by hand, no library |
| message | `id` (UUIDv7 or v4), `type` BOOKING/CHANGE/RENEWAL/CANCELLATION, `status` NEW/TO_BE_SENT/SENT/FAILED, `files[]` | nothing ever leaves the process |
| task, user, role event, integration, export, audit row | see the endpoint table | |

### Subscription lifecycle

`config/subscription.machine.yaml` is the single source; screens render its action
names as `data-action`:

```
draft --submit--> pending_payment --confirm--> active
draft --activate (activate_immediately)--> active
active|renewed --change|cancel|renew--> changing
changing --confirm_change--> active   --confirm_cancel--> cancelled
         --confirm_renew--> renewed   --discard_draft--> previous status
suspended --resume--> active            (on purpose, nothing leads into suspended)
```

* **Confirm** schedules one transaction per period of the first term (`term_end` =
  start + `term_months`, 12 by default, month-end clamped); the first one carries the
  plan's setup fee. It issues the *account opened* letter.
* **Change** writes version *n+1* effective on `effective_date`, re-prices unbooked
  later periods and books one `CHANGE` transaction from the effective date to the end of
  the period that contains it, worth the pro-rata share of the price delta (a change
  without a price move still books a zero `CHANGE`). `currency`, `plan_id` and
  `frequency` are locked: editing one answers 422 with
  `fields.<name> = "<name> cannot be edited in a change."`. Moving a subscription to
  another customer adds the draft step `ownership_acceptance`; confirming before
  `POST /api/drafts/{id}/actions/accept-ownership` answers 422. The start date can move
  until the first booking. It issues the *plan update notice*.
* **Cancel** (the screens say *end subscription*) needs a reason (`NO_LONGER_NEEDED`,
  `BUDGET`, `CONSOLIDATING`, `OTHER`), credits the unearned part of booked periods
  after the effective date under the `day_count` setting, voids later unbooked
  periods, and needs an account for a non-zero credit (the direct-debit account, else
  the customer's payout account). It issues the *closure notice*.
* **Renew** is allowed within `renewal_window_days` of the term end once every period
  before it is booked; the new term is one calendar year (29 Feb clamps to 28 Feb) and
  the price is indexed `monthly x (1 + index% / 100)`. It issues the *term extension
  notice*.
* **Note** keeps a title and a description; it never changes status or version.

### Customer and subscription rules (message catalogues in `customers.MESSAGES`, `subscriptions.MESSAGES`)

* Postal code: free form, 1–12 letters, digits, spaces or hyphens, starting with a
  letter or digit. Country: two upper-case letters (home country `XA`).
* Payout account and direct-debit account: the public mod-97 check digits.
  `customers.make_iban()` builds valid invented numbers; never use real ones.
* A subscription needs a verified customer (setting `require_verified_customer`).
* Invoice delivery by e-mail (the default) needs an e-mail address on the customer.
* `payment_method.type` is CARD, BANK_TRANSFER or DIRECT_DEBIT; only DIRECT_DEBIT
  needs an account. `po_reference` is at most 35 characters. Labels come from a
  catalogue (`L-PILOT`, `L-INTERNAL`, `L-GRANT`).
* A reseller share above `commission_cap_percent` is refused.
* Numbers are issued by the service (`TYPE-D1-n`, strictly increasing per type); a
  number a client sends is ignored.

## Endpoints

All JSON. Writes answer 201 on create, 200 otherwise; errors are
`{"error", "message", ...}` with `fields` on a 422. `openapi.json` has the full list
with request bodies.

| Area | Endpoints |
|---|---|
| control | `GET /api/_admin/state`, `POST /api/_admin/reset`, `GET/POST /api/_admin/clock`, `GET/POST /api/_admin/faults`, `GET/PATCH /api/_admin/settings`, `POST /api/_admin/scheduler-mode` |
| auth | `POST /oauth/token` (password, client_credentials), `GET /api/me` |
| machine | `GET /api/machines/{key}` |
| customers | `POST/GET /api/customers`, `GET/PATCH /api/customers/{id}`, `POST /api/customers/{id}/actions/verify`, `POST /api/account-check` |
| reference | `GET/POST /api/reseller-terms`, `/api/partners`, `/api/plans` (+ `/{id}`) |
| subscriptions | `POST/GET /api/subscriptions`, `GET/PATCH /api/subscriptions/{id}` (`?version=n`), `GET .../versions`, `POST .../actions/submit`, `POST .../actions/confirm`, `POST .../drafts`, `GET .../transactions`, `GET .../notes`, `GET/PATCH /api/drafts/{id}`, `POST /api/drafts/{id}/actions/{confirm,discard,accept-ownership}`, `POST /api/search` (keyset) |
| billing | `POST /api/quotes/calculate`, `GET .../invoices`, `POST .../invoices/process?until=`, `GET /api/invoices/{id}`, `GET /api/invoices/{id}/journal`, `POST /api/invoices/{id}/pay`, `GET /api/journal?references=`, `GET /api/payments/statement` |
| finance admin | `GET /api/ledger-codes`, `GET /api/accounts`, `PATCH /api/accounts/{id}`, `GET/POST /api/settlements` (`?party=&tab=`), `POST /api/settlements/{id}/actions/close`, `GET /api/statements?settlement=` |
| jobs | `GET /api/jobs` (`?after=&limit=`), `GET /api/jobs/{id}`, `POST /api/jobs/run`, `POST /api/jobs/billing/run-next`, `POST /api/jobs/renewals/run`, `GET /api/events` |
| documents | `GET /api/templates`, `GET /api/templates/{id}/source`, `POST /api/render`, `GET .../documents`, `GET /api/documents/{id}`, `GET /api/documents/{id}.pdf`, `GET /api/files`, `GET /api/files/{id}` |
| messages | `GET /api/messages` (`?subscription_id=&since=`), `POST /api/messages/generate` (202 + job), `POST /api/messages/send`, `GET /api/messages/{id}`, `GET /api/messages/{id}/content` (`?format=xml`; 404 while NEW) |
| admin | `GET/POST /api/tasks`, `POST /api/tasks/{id}/actions/complete`, `GET /api/users`, `POST /api/users/{id}/roles`, `GET /api/users/{id}/role-history`, `GET /api/integrations/services`, `GET /api/integrations`, `POST /api/integrations/{svc}`, `GET/POST /api/exports`, `GET /api/exports/{id}`, `GET /api/exports/{id}/download` (zip), `GET /api/audit` |
| platform | `GET /api/health` |

**Gateway.** `/gw/<area>/...` forwards to `/api/<area>/...` for the areas customers,
documents, invoices, jobs, messages, subscriptions and tasks (`X-Demo-Gateway:
forwarded`). Anything else gets the gateway's own 404 `{"error": "no_route", "source":
"gateway"}` with `X-Demo-Source: gateway`; a missing record behind a forwarded route is
the service's `{"error": "not_found"}` with `X-Demo-Source: service`. An unknown `/api`
path is `{"error": "unknown_path"}`.

## Documents

| Template | Issued on |
|---|---|
| `order_summary` | submit |
| `account_opened` | confirm / activate |
| `plan_update_notice` | confirm_change |
| `closure_notice` | confirm_cancel |
| `term_extension_notice` | confirm_renew |
| `invoice` | booking |

Template syntax: `{{path}}`, `{{path|money}}` (`EUR 12.30`), a leading `?{path}` for a
line printed only when the value is set, and `## ` for a section (`## Details`).

## Screens

Server-rendered HTML with small inline scripts. Every element a test needs has a
`data-testid` (plus a role or label where one applies); lifecycle buttons carry
`data-action` named as in the machine; status pills carry `data-state`. Every page
shows the fault banner (`demo-faults-banner`, attribute `data-faults`), a side menu
(`side-menu`, `menu-*`) and an error banner (`error-banner`, `role=alert`) that scripts
fill when an API call fails.

| Path | What |
|---|---|
| `/login` | login form (`login-username/password/submit`, `login-error`) |
| `/ui` | dashboard with counts |
| `/ui/subscriptions` | list: filter chip (`filter-input`, `filter-chip`), keyset paginator (`page-next/prev`) over `POST /api/search` |
| `/ui/subscriptions/new` | wizard *Parties* step: reseller/customer reference picker dialog (`picker-dialog`, `picker-row`, `picker-choose`, `*-change`; Enter in `picker-search` searches, a click on a row picks it), direct customer-number entry (`customer-number`, `customer-number-lookup`, `customer-number-status`), screening step, billing address block (`address-block`, `data-country`), plan/frequency/start/data region/workspace/invoice delivery |
| `/ui/subscriptions/{id}/step/quote` | price form (`price-net`, `price-tax-rate`, `price-commission`, `price-calculate`, inline `error-*`, `activate-immediately`, `price-submit`) |
| `/ui/subscriptions/{id}/step/confirm` | confirm step |
| `/ui/subscriptions/{id}?version=n` | detail: status pill, version selector (`version-select`, loads that version over the API), actions, `#ledger` grid, `#downloads` (`download-document`), `#messages` (`generate-messages` polls the job, `download-message`), notes |
| `/ui/drafts/{id}` | change / end-subscription / renew / note steps; renewal has an indexation step and a price step in browser history (`?step=price`) |
| `/ui/customers`, `/ui/customers/new`, `/ui/customers/{id}/edit` | customer form with a billing address block and field errors (`error-billing_address-postal_code`) |
| `/ui/tasks` | task list with local date/time in `ui_timezone`, row menu, create form |
| `/ui/admin` | cards; *Archived lists* is retired from the cards but `/ui/admin/archived-lists` still answers with a breadcrumb |
| `/ui/admin/accounts` (+ `/{id}/edit`) | account rows with a row menu; *Edit* is disabled exactly for managed accounts |
| `/ui/admin/integrations` | forms rendered from the services response; optional `baseUrl` is not required and has a hint |
| `/ui/admin/settlements` | Open/Closed tabs, party filter that narrows locally and queries only on selection |
| `/ui/admin/{plans,partners,jobs,users,exports}` | plain tables |

## Clock

`POST /api/_admin/clock` with `{"now": "2026-06-03T09:00:00Z"}` freezes it,
`{"shift_days": 1}` moves it, `{"reset": true}` returns to real time. Every response
carries `X-Demo-Clock: <instant>; frozen|real`.

## Fault switches

Off by default; `POST /api/_admin/faults` with `{"name": true}` or
`{"active": ["a", "b"]}`. **Every response carries `X-Demo-Faults`** (`none` or the
sorted list) and every screen shows the banner, so evidence always records the fault
set.

| Switch | Defect |
|---|---|
| `api_unavailable` | `/api` and `/gw` answer 503 (control routes excepted); screens show the outage banner |
| `negative_price_accepted` | the price calculation lets a negative net amount through |
| `rounding_drift` | the price calculation cuts at the cent instead of rounding half-up |
| `screen_filter_unlabelled` | the subscription list's filter has no label or accessible name (an accessibility defect) |
| `refund_tax_applied_twice` | a closure credit takes its tax leg from the gross unearned amount: tax on tax |
| `refund_legs_rounded_independently` | a closure credit's total is rounded from the unrounded sum, its legs one by one, so they can miss by a cent |
| `proration_floors_to_zero` | a mid-period price change counts whole periods only and books 0.00 |
| `effective_date_day_month_swapped` | a start-date change reads day and month the other way round when both are 12 or less |
| `date_parser_rejects_leap_day` | a start date of 29 February answers 422 |
| `index_percent_truncated` | renewal indexation drops the decimals of the percentage (2.5 % becomes 2 %) |
| `payment_replay_double_counted` | paying twice with one `Idempotency-Key` records two payments |
| `message_send_fails_on_non_ascii` | the send writes the text attachment as ASCII, so a name such as Zoë fails the send |
| `xml_escapes_twice` | outbound XML escapes invoice-note text twice (`&` arrives as `&amp;amp;`) |
| `run_next_rebooks_claimed_job` | `run-next` does not claim the transaction, so a second call in the same tick books it again |
| `customer_read_cached_after_update` | `GET /api/customers/{id}` serves the record as it was before the last update (the list is not cached) |
| `customer_name_truncated` | display names of 41–64 characters are stored cut to 40, without an error |
| `role_history_sorted_as_text` | role history is ordered by event id compared as text (10 before 9) |
| `reseller_share_scaled_twice` | reseller terms store `share_percent` 6 as 0.06 and settlement applies it as a percent |
| `document_renders_previous_version` | the plan update notice is rendered from version n-1 |
| `doc_optional_prints_none` | without a billing contact the account-opened letter prints `Billing contact: None` |
| `doc_amount_unformatted` | the account-opened letter prints the charge per period as a bare number |
| `form_resets_select_after_error` | after a validation error on the closure screen the reason list jumps back to its first option |
| `create_throttled_every_other` | every second subscription create answers 429 with `Retry-After: 0` and stores nothing |
| `task_title_presence_only` | a task title is checked for presence only, so `""` or a blank title is stored |

## Settings

`GET/PATCH /api/_admin/settings`: `scheduler_mode` (all | one_at_a_time),
`send_order` (v1 type+id | v2 type+name), `message_id_scheme` (uuid7 | uuid4),
`day_count` (actual/period | 30/360), `index_percent` (2.5), `renewal_window_days`
(90), `payment_term_days` (30), `commission_cap_percent` (20), `ui_timezone`
(Europe/Berlin), `screening_label` (Run screening | Start check), `auth_required`
(false), `require_verified_customer` (true), `deployed_at`, `background_delay_ms`
(150), `account_check_mismatch_once` (accounts whose first name check answers
NO_MATCH). Demo users: `demo-admin` / `demo-clerk`, password `demo-password`; client
`demo-client` / `demo-client-secret`. These are throwaway local values.

## Pytest fixtures (`demo_sut.pytest_plugin`)

```python
# conftest.py
from demo_sut.pytest_plugin import demo_control, demo_http, demo_seed, demo_sut  # noqa: F401


def test_credit(demo_sut, demo_control, demo_seed):
    demo_control.freeze("2026-06-03T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-06-01")
    demo_seed.book_due(sub["id"])
    draft = demo_seed.draft(sub["id"], "cancel", effective_date="2026-06-16", reason="BUDGET")
    demo_seed.confirm_draft(draft["id"])
```

* `demo_sut` (session): starts the demo on `127.0.0.1:<free port>` in a thread with a
  fresh database (per xdist worker); `DEMO_SUT_URL` points it at an instance that is
  already running. Handle: `base_url`, `api_url`, `client`, `db_path`, `server`.
* `demo_control` (function): `freeze`, `shift`, `faults(*names)`, `fault(name)`,
  `settings(**kw)`, `reset_database()`; puts back the real clock, no faults and default
  settings at teardown and records the faults seen during the test as the
  `demo_faults` user property.
* `demo_seed` (function): `DemoSeeder` – `customer()`, `partner()`,
  `subscription(state=...)`, `book_due()`, `draft()`, `confirm_draft()`.
* `demo_http` (function): the project's `HttpEngine` on `/api/`, observed by
  `demo_control` for fault evidence.
