| Risk | Test |
|---|---|
| The API is down or failing | `tests/api/test_health.py::test_api_answers_without_a_server_error` |
| An unknown route crashes instead of answering 404 | `tests/api/test_health.py::test_unknown_route_is_a_client_error_not_a_crash` |
| The client retries a write and duplicates data | `tests/unit/test_http_client.py` |
| A response drifts from the documented contract | `tests/unit/test_items_service.py` |
| Invalid input is accepted | `tests/unit/test_models.py` |
