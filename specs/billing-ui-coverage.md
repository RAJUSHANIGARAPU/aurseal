---
seed: true
feature: billing-ui-coverage
---

# Billing screens: coverage analysis

A coverage analysis of the demo system's billing screens (`demo_sut`, see
`docs/features/demo-sut.md`): what each screen renders, what the demo examples
already verify, what is left, in priority order, and the locators a browser test
would need. It is a planning document, kept next to the specs so the backlog is
reviewed with them; the scenarios at the end are in the spec format the
generator reads.

## What the screens render

| Screen | Path | Billing content | Stable hooks |
|---|---|---|---|
| Subscription detail | `/ui/subscriptions/{id}` | status pill, monthly gross, ledger grid (kind, period start/end, net, tax, fee, total, state per transaction), documents table with downloads, version selector | `status-pill` (`data-state`), `monthly-gross`, `ledger-grid`, `ledger-row` (`data-transaction-id`, `data-state`), `td[data-col=...]`, `documents-table`, `download-document`, `version-select` |
| Quote step | `/ui/subscriptions/{id}/step/quote` | price form: net, tax rate, commission, calculated result, inline field errors | `price-net`, `price-tax-rate`, `price-commission`, `price-calculate`, `price-gross`, `price-tax-amount`, `price-commission-amount`, `error-net_amount`, `error-tax_rate`, `error-commission_percentage`, `price-submit` |
| Confirm step | `/ui/subscriptions/{id}/step/confirm` | final submit that schedules the first term | `step-confirm`, `monthly-gross`, `action-confirm` (`data-action="confirm"`) |
| Ledger accounts | `/ui/admin/accounts` | account number, name, managed flag; *Edit* disabled for managed accounts | `accounts-table`, `account-row`, `account-number`, `account-managed`, `account-menu`, `account-edit` (`disabled` when managed) |
| Settlements | `/ui/admin/settlements` | open/closed tabs, party filter, closing balance | tab and filter controls |

The ledger grid and the documents table are rendered on the server, so their
content can be read over HTTP; the price form, the version selector and the
settlement filter run scripts and need a browser.

## What the examples verify today

| Behaviour | Example | Level |
|---|---|---|
| Ledger grid equals the confirm call's schedule, row by row; rows add up, are contiguous and monthly | `tests/demo_examples/finance/test_invoice_grid_matches_api.py` | HTTP (server-rendered grid) |
| Ledger grid (deep link `#ledger`) equals the API ledger after the first booking | `tests/demo_examples/finance/test_ledger_grid.py` | HTTP |
| Invoice and journal integrity, invoice date rules | `test_invoice_integrity.py`, `test_invoice_dates.py` | API |
| Managed accounts refuse a rename, unmanaged ones accept it | `test_accounts_rename.py` | API |
| Statement export: one signed entry per settlement | `test_finance_api_delta.py` | API |
| A closure's journal entry balances and matches the closure notice's credit | `test_closure_journal.py` | API + PDF text |
| Document totals and settlements against the ledger | `tests/demo_examples/documents/` | API + PDF text |

## Backlog, by priority

1. **Edit disabled exactly for managed accounts** (accounts screen). The API
   refusal is covered; the screen could still offer *Edit* and fail on save.
   Needs a browser. Locators: `account-row` with its `account-number`, `account-menu`,
   `account-edit` and its `disabled` / `aria-disabled` state.
2. **Price form result equals the price calculation** (quote step), including the
   rounding rule and the commission cap error. Needs a browser: the result is
   filled in by a script. Locators: `price-*`, `error-commission_percentage`.
3. **Version selector shows the version's own monthly gross** (detail screen)
   after a change. Needs a browser. Locators: `version-select`, `monthly-gross`,
   `version-status`.
4. **Settlement party filter narrows locally and queries only on selection**
   (settlements screen). Needs a browser and request capture.
5. **Document download from the documents table is the stored file** (detail
   screen). Covered at API level by the document round trip; a browser check adds
   only the download wiring. Lowest priority.

## Scenario Ledger grid matches the schedule

### Preconditions
- A subscription confirmed through the API with the clock frozen

### Steps
1. Capture the confirm response's scheduled transactions
2. Open the subscription detail screen at `#ledger`
3. Read every `ledger-row`

### Expected
- One row per scheduled transaction, in period order, with the same kind, period, amounts and state
- Periods are contiguous and one month long; only the first carries the one-off fee

### Tags
`billing` `ledger`

## Scenario Managed account cannot be edited

### Preconditions
- The seeded ledger accounts

### Steps
1. Open the ledger accounts screen
2. Open the row menu of a managed account
3. Open the row menu of an unmanaged account

### Expected
- *Edit* is disabled for the managed account and enabled for the unmanaged one

### Tags
`billing` `accounts`
