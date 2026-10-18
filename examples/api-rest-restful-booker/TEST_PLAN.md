# Test plan: Restful Booker

## Scope

- **In scope:** `POST /auth`, and create, read, update, partial update, delete and filter on
  `/booking`.
- **Out of scope:** `/ping`, load, and security testing beyond the token check. Load is
  out because the API is a shared public demo; the rest are next steps in the README.

## Risks and priority

| Risk | Likelihood | Impact | Priority | Covered by |
|---|---|---|---|---|
| A booking is changed or deleted without a token | L | H | P1 | `test_update_booking_without_token_is_rejected_403` |
| A write loses or alters a field | M | H | P1 | `test_update_replaces_every_field`, `test_partial_update_changes_only_supplied_fields` |
| A response drifts from the documented shape | M | M | P2 | `tests/unit/test_booking_contract.py`, schema check on every live answer |
| Invalid input is stored | M | M | P2 | `test_malformed_dates_are_rejected` (finding 1) |
| A client mistake crashes the server | M | M | P2 | `test_missing_required_fields_are_a_client_error` (finding 2) |

## Automated vs exploratory

- **Automated:** every endpoint above, the token rule for every write method, the contract.
- **Exploratory:** filters by date, very long names, concurrent updates of one booking.

## Entry and exit criteria

- **Entry:** `POST /auth` returns a token for the documented admin account.
- **Exit:** every P1 test passes; findings are recorded as strict expected failures, so a
  fix on the server turns them into a visible change rather than a silent pass.

## Environments and data

- **Target:** <https://restful-booker.herokuapp.com>, nightly. Anyone can write to it, so
  every test creates its own booking with a unique name, asserts only on that booking, and
  deletes it afterwards. No test counts rows.
- **Data:** built by `api_rest_restful_booker.data.bookings.a_booking`.

## Known limits

- The host can be slow to wake up; GET requests retry with backoff, writes never do.
