# Countries GraphQL

> Country lookups and schema drift checks on the public Countries GraphQL API.

A worked example of the `api-graphql` template: the skeleton is rendered from it unchanged
(the files listed in [.scaffold-manifest](.scaffold-manifest)); the operations, models,
service and tests are written for this API.

## Run it

```bash
make test        # installs dependencies on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`. Or, with only Docker:
`docker compose run --rm tests`. Expected runtime: about 10 seconds.

`make test-offline` validates every operation against [schema.graphql](schema.graphql) and
runs the client and service tests on `httpx.MockTransport`, with no network. The live tests
run against <https://countries.trevorblades.com> every night, never on a pull request: a
third-party outage is not a defect in this code.

## Decisions and trade-offs

| Decision | Alternatives considered | Why | Cost accepted |
|---|---|---|---|
| Operations in `.graphql` files, validated offline against a committed snapshot | Inline query strings; validating against live introspection | A typo or a removed field fails in the offline tier, before any request | The snapshot is re-recorded by hand when the API changes on purpose |
| Live drift test: introspect, diff against the snapshot, name breaking changes first | Trusting the snapshot | The API has one maintainer and no changelog; drift shows up as a reviewable diff | Any harmless schema addition fails the nightly run until recorded |
| Assert only on stable facts (capital, currency, an official language) | Asserting whole payloads; counting countries | Facts like these change rarely and visibly; payloads and counts do not | Less is asserted per call |
| List queries select codes and names only | Selecting nested states and languages | A public service pays for every resolver; the filter is what is under test | Nested fields of a listing are not checked |
| Strict, frozen pydantic models per selection set | One model per schema type | A model that matches the selection catches a dropped or renamed field at once | A model per operation shape |

## Scope

**In scope:** `country(code)`, `countries(filter: { continent })`, request validation errors,
and the schema as a whole (drift).

**Out of scope**, and the risk each leaves:

- `continents`, `languages` and the other filters: their resolvers are not exercised.
- Load and query cost: the service publishes no limits, and a shared free API is not the
  place to find them.
- Localised names (`name(lang:)`): translations change without notice.

## What the tests cover

| Risk | Test |
|---|---|
| A lookup returns wrong or missing data | `tests/api/test_countries.py::test_country_matches_the_known_facts` (NL, JP, BR, KE) |
| An unknown code errors or invents a country | `test_unknown_country_code_returns_null_not_error` |
| A filter leaks rows from other continents | `test_countries_filtered_by_continent_all_belong_to_it`, `test_a_filter_matching_nothing_is_an_empty_list` |
| A bad request is executed instead of rejected | `test_a_missing_required_variable_is_rejected`, `test_a_wrongly_typed_argument_is_rejected` |
| The schema changes under the operations | `tests/api/test_endpoint.py::test_live_schema_matches_the_snapshot` |
| An operation stops fitting the schema | `tests/unit/test_operations.py` (offline) |
| A response drifts from the model | `tests/unit/test_countries_service.py` (offline) |

## How to read the report

- **Locally:** `reports/report.html`, one self-contained file.
- **A failed test** shows the captured log: every request with its status. A GraphQL error
  lists the server's `errors[]` messages; a drift failure prints the breaking changes, then
  the full diff against `schema.graphql`.

## Findings

| # | Finding | Severity | Steps to reproduce | Evidence |
|---|---|---|---|---|
| 1 | Country codes are case-sensitive: `nl` answers `null`, the same as a code that does not exist | Low | `{ country(code: "nl") { name } }` | `test_country_codes_are_case_sensitive` |
| 2 | Request errors use two status codes: a validation error answers HTTP 200, a missing variable HTTP 400 | Low | `{ nope }` and `query($c: ID!) { country(code: $c) { name } }` without variables | Both raise `GraphQLResponseError`; seen with `curl -w '%{http_code}'` on 2026-10-18 |

## What I would do next

1. Run the API from its source in a container for pull requests, so the live tests also
   run per change without the public host.
2. Cover `continents` and `languages`, with one stable fact each.
3. Record the schema drift as a dated report in the nightly run, so harmless additions are
   reviewed in a batch instead of failing the night they appear.

## Project layout

```text
schema.graphql  schema snapshot, recorded from the live API
src/graphql_countries/
  client/       gql over httpx: timeouts, redacted logging, errors[] raised as one type
  operations/   country_by_code.graphql, countries_in_continent.graphql
  services/     countries: by code, by continent; return models
  models/       pydantic models, one per selection set
  data/         stable facts the live tests check
  contract.py   introspection, snapshot diff, `--record`
tests/
  api/          live tests against the API
  unit/         offline tests on httpx.MockTransport and the snapshot
```
