# Restful Booker

> Authentication and booking CRUD on the public Restful Booker API.

A worked example of the `api-rest` template: the skeleton is rendered from it unchanged
(the files listed in [.scaffold-manifest](.scaffold-manifest)), the models, services and
tests are written for this API.

## Run it

```bash
make test        # installs dependencies on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`. Or, with only Docker:
`docker compose run --rm tests`. Expected runtime: about 10 seconds.

`make test-offline` runs the client, contract and model tests on `httpx.MockTransport`,
with no network. The live tests run against <https://restful-booker.herokuapp.com> every
night, never on a pull request: a third-party outage is not a defect in this code.

## Decisions and trade-offs

| Decision | Alternatives considered | Why | Cost accepted |
|---|---|---|---|
| Client, then services, then tests | Raw `httpx` calls in tests | Timeouts, retries and logging live once; tests read as behaviour | One more layer between a test and the wire |
| Pydantic models and a JSON Schema contract on every answer | Asserting on raw dicts | The schema catches drift pydantic would coerce away (`"111"` for `111`) | Two descriptions of one payload |
| Each test creates, owns and deletes its bookings | Shared seed data | Anyone can write to this API; a test that counts rows breaks when a stranger posts | More requests per test |
| Retries only for GET | Retry every request | Repeating a POST here creates a second booking | A failed write surfaces at once |
| Known defects as strict expected failures | Skipping them; leaving them red | The suite stays green while the defect is open, and fails the day it is fixed, so the test is updated | Two tests that pass by failing |

## Scope

**In scope:** `POST /auth`, and create, read, update, partial update, delete and filter on
`/booking`.

**Out of scope**, and the risk each leaves:

- Load: a shared public demo is not the place; latency under concurrency is not measured.
- Security beyond the token: injection and authorisation gaps go undetected.
- Filters by check-in and check-out date.

## What the tests cover

| Risk | Test |
|---|---|
| A write without a token succeeds | `tests/api/test_bookings.py::test_update_booking_without_token_is_rejected_403` (PUT, PATCH, DELETE) |
| A write loses or alters a field | `test_update_replaces_every_field`, `test_partial_update_changes_only_supplied_fields` |
| Created data is not what was sent | `test_create_booking_returns_id_and_echoes_payload`, `test_get_returns_the_booking_as_created` |
| A booking cannot be found by name, or survives deletion | `test_list_bookings_filtered_by_name_contains_created_booking`, `test_deleted_booking_is_gone` |
| The wrong credentials get a token | `tests/api/test_auth.py` |
| A response drifts from the contract | `tests/unit/test_booking_contract.py` (offline) |
| The client retries a write | `tests/unit/test_http_client.py` (offline) |

## How to read the report

- **Locally:** `reports/report.html`, one self-contained file.
- **A failed test** shows the captured log: every request and response line, with the
  token replaced by `***`.

## Findings

| # | Finding | Severity | Steps to reproduce | Evidence |
|---|---|---|---|---|
| 1 | Malformed dates are accepted and stored as `0NaN-aN-aN` | Medium | `POST /booking` with `"checkin": "not-a-date"` | 200 with the stored booking; `test_malformed_dates_are_rejected` |
| 2 | A booking without required fields answers 500, not 400 | Medium | `POST /booking` with only `firstname` | 500 Internal Server Error; `test_missing_required_fields_are_a_client_error` |
| 3 | Wrong credentials answer 200 with `{"reason": "Bad credentials"}`, not 401 | Low | `POST /auth` with a wrong password | `tests/api/test_auth.py` asserts the reason, since no status distinguishes it |

## What I would do next

1. Run the suite against the API's own Docker image on every pull request, so test changes
   are checked without the public host.
2. Cover the date filters, with bookings created on known dates.
3. A contract test generated from an OpenAPI description, if the API published one.

## Project layout

```text
schemas/        JSON Schema contracts: booking, created booking
src/api_rest_restful_booker/
  client/       HTTP client: timeouts, GET retries, redacted logging
  services/     auth and bookings; return models
  models/       pydantic models of a booking
  data/         booking builder with a unique name per call
  contract.py   JSON Schema validation
tests/
  api/          live tests against the API
  unit/         offline tests on httpx.MockTransport
```
