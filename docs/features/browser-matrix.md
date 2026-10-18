# Browser matrix

## What it does

Runs the smoke selection on chromium, firefox and webkit as separate CI legs, and
keeps the results honest per engine:

- **Engine identity** (`gate.browsers`): `browser_from_nodeid` reads the engine out of
  a node id (`test_x[firefox]`, `test_x[a-webkit]`); `strip_browser_param` removes it
  so `test_x[firefox]` and `test_x[chromium]` are recognised as one test (for history
  and the CI-selection gate); `item_browser` reads the `browser_name` parameter of a
  collected item.
- **Results carry the engine**: every browser test gets a `browser` Allure label and a
  `("browser", <engine>)` user property, which the results store records on the
  result row. A failure on webkit alone reads as a webkit failure.
- **Known defects scoped by engine**: `@pytest.mark.known_defect("BUG-9", ...,
  browsers=("webkit",))` is expected-red on webkit only. On the other engines the
  marker does not apply and the test must pass like any other test, so a leg is never
  muted for a defect it does not have. An unknown engine name is a usage error.
- **CI-selection gate**: `--browser` and `--browser-channel` are execution-only, and
  engine variants of a node id count as one test, so a firefox-only leg still covers
  the smoke tests, and a typo marker on that leg still reports a gap.

## How to use

```bash
pytest -m smoke -n auto --browser firefox
pytest -m smoke -n auto --browser webkit
pytest -m smoke -n auto --browser chromium --browser-channel=chrome   # branded Chrome
```

```python
@pytest.mark.known_defect(
    "BUG-9", raises=AssertionError, match=r"menu overlaps the footer", browsers=("webkit",)
)
def test_menu_layout(page): ...
```

## Configuration

| Item | Default | Meaning |
|---|---|---|
| `--browser` | `chromium` | Engine (pytest-playwright); repeatable |
| `--browser-channel` | none | Branded build of an engine (e.g. `chrome`); chromium only |
| `known_defect(browsers=...)` | all engines | Engines the defect applies to |

The matrix job is blocking (no `continue-on-error`) and runs on `main` pushes and the
nightly schedule, not on every pull request.

## Example

```text
ui/tests/test_search.py::test_search[chromium] PASSED
ui/tests/test_search.py::test_search[firefox]  PASSED
ui/tests/test_search.py::test_search[webkit]   XFAIL (known defect BUG-9 is open)
```

## Honest limits

- Desktop engines only. Viewport or user-agent emulation is not real-device coverage.
- Expect a stabilisation period: firefox and webkit expose real rendering and timing
  differences against a live site, which is signal, not noise.
- After `--browser-channel=chrome` leaves `addopts`, the chromium leg uses the bundled
  chromium unless the flag is kept on that leg's command.
