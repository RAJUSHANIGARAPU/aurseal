| Risk | Test |
|---|---|
| The app does not start | `tests/mobile/test_launch.py::test_app_launches_to_home_screen` |
| An invalid sign-in is accepted silently | `tests/mobile/test_login.py::test_invalid_login_shows_inline_error` |
| A tab no longer opens its screen | `tests/mobile/test_launch.py::test_tab_bar_opens_each_screen` |
| A capability is missing or wrong | `tests/unit/test_capabilities.py` (offline) |
| Screen objects drift from the app's accessibility ids | `tests/unit/test_screens.py` (offline, fake driver) |
