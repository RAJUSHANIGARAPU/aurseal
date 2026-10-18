| Risk | Test |
|---|---|
| The site does not load or has no title | `tests/ui/test_home.py::test_home_page_loads_with_a_title` |
| Scripts fail while the page loads | `tests/ui/test_home.py::test_home_page_loads_without_console_errors` |
| The layout overflows on small screens | `tests/ui/test_home.py::test_home_page_fits_the_viewport` |
| Links a screen reader cannot announce | `tests/ui/test_home.py::test_every_navigation_link_has_an_accessible_name` |
| Page objects drift from the markup | `tests/unit/test_login_page.py` (offline, local HTML fixture) |
