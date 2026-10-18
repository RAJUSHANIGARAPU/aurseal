# Demo examples

Example tests that run the framework's features against the in-repo demo service
(`demo_sut/`, see [demo-sut.md](demo-sut.md)). They live under `tests/demo_examples/<area>/`;
each area documents its examples on its own page, `demo-examples-<area>.md`. This page holds
the generator examples.

API-level examples run in the offline suite (`pytest tests/ -n auto`). Browser examples carry
the `demo_ui` marker and run in the browser lane:

```bash
pytest tests/demo_examples -m demo_ui
```

## Generators: config-driven cases

**What it shows.** `tests/demo_examples/generators/test_config_driven_suite.py` generates the
cases of [Config-driven cases](generators.md#config-driven-cases) from the demo's machine file
and runs them against the demo. The key, consistency and declared-action cases run offline;
`initial_reachable` also reads `GET /api/machines/subscription` and compares the served initial
states. Every `action_renders` case puts a fresh subscription in its state and asserts the
screen shows `[data-action="<name>"]` (on the detail screen, or the draft screen for
`changing`); these are `demo_ui`.

**How to run it.**

```bash
pytest tests/demo_examples/generators/test_config_driven_suite.py -m "not demo_ui"
pytest tests/demo_examples/generators/test_config_driven_suite.py -m demo_ui
```

**Configuration.** None. The adapter (`tests/demo_examples/generators/_demo_machine.py`)
freezes the demo clock at 2026-03-10 and starts yearly subscriptions on 2025-03-15.

## Generators: traversal paths and the unreachable state

**What it shows.** `test_state_paths.py` walks a covering set of traversal paths, one
transition at a time, and checks the status pill shows `[data-state="<target>"]` after each.
Transitions are driven over the demo's API, the same calls its buttons make. Before a
renewal the clock moves into the renewal window and due periods are booked, so a second
renewal after a renewal is walkable too. Paths that confirm a draft of another kind are
dropped by `feasible`. `test_state_reachable` has one case per state; `suspended` is
unreachable and is reported through `known_defect("BUG-101")`.

```bash
pytest tests/demo_examples/generators/test_state_paths.py -m demo_ui
```

## Generators: zero-code factory

**What it shows.** `test_yaml_generated.py` registers the machine file in `WORKFLOW_FILES` and
lets `generators.yaml_suite.pytest_generate_tests` expand it into all of the above. The one
test function runs every generated case against the demo.

```bash
pytest tests/demo_examples/generators/test_yaml_generated.py -m demo_ui
```
