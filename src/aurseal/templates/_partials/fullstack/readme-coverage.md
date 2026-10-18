| Risk | Test |
|---|---|
| Data created through the API never reaches the screen | `tests/ui/test_items_seam.py::test_item_created_via_api_appears_in_the_ui_list` |
| The site does not load or has no title | `tests/ui/test_home.py::test_home_page_loads_with_a_title` |
| The API is down or failing | `tests/api/test_health.py::test_api_answers_without_a_server_error` |
| Page objects drift from the markup | `tests/unit/test_login_page.py`, `tests/unit/test_items_page.py` (offline) |
| A response drifts from the documented contract | `tests/unit/test_items_service.py` (offline) |
| Test data collides between parallel tests | `tests/unit/test_item_builder.py` (offline) |
