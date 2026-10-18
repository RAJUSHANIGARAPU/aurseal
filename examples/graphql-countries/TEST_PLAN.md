# Test plan: Countries GraphQL

## Scope

- **In scope:** `country(code)`, `countries(filter: { continent })`, request validation, and
  the schema as a whole.
- **Out of scope:** the other root fields and filters, localised names, load. Load is out
  because the API is a free shared service; the rest are next steps in the README.

## Risks and priority

| Risk | Likelihood | Impact | Priority | Covered by |
|---|---|---|---|---|
| The schema changes and an operation silently stops working | M | H | P1 | `test_live_schema_matches_the_snapshot`, `tests/unit/test_operations.py` |
| A lookup returns wrong data | L | H | P1 | `test_country_matches_the_known_facts` |
| An unknown code errors instead of answering null | L | M | P2 | `test_unknown_country_code_returns_null_not_error` |
| A filter returns rows outside the filter | L | M | P2 | `test_countries_filtered_by_continent_all_belong_to_it` |
| An invalid request is executed | L | M | P2 | `test_a_missing_required_variable_is_rejected`, `test_a_wrongly_typed_argument_is_rejected` |

## Automated vs exploratory

- **Automated:** the two operations, their null and empty paths, request validation, drift.
- **Exploratory:** case handling of codes (finding 1), status codes of errors (finding 2),
  nested queries and their response time.

## Entry and exit criteria

- **Entry:** `{ __typename }` answers `Query`.
- **Exit:** every P1 test passes; a drift failure is either recorded (`--record`, with the
  diff in the pull request) or reported upstream.

## Environments and data

- **Target:** <https://countries.trevorblades.com>, nightly. Read-only, static data: tests
  share nothing and can run in any order.
- **Data:** stable facts in `graphql_countries.data.countries`.

## Known limits

- One maintainer and no changelog: the snapshot is the only record of what changed.
