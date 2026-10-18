| Risk | Test |
|---|---|
| The endpoint is down or not GraphQL | `tests/api/test_endpoint.py::test_endpoint_answers_a_typename_query` |
| A query error crashes the server instead of answering `errors[]` | `tests/api/test_endpoint.py::test_unknown_field_is_reported_in_errors_not_a_crash` |
| The schema changed under the operations | `tests/api/test_endpoint.py::test_live_schema_matches_the_snapshot` |
| An operation no longer fits the schema | `tests/unit/test_operations.py` (offline, against `schema.graphql`) |
| `errors[]`, HTTP errors and non-JSON answers are swallowed | `tests/unit/test_graphql_client.py` |
| A response drifts from the model | `tests/unit/test_items_service.py` |
