# Workflow generators

The `generators/` package reads a declared workflow (a state machine in YAML) and turns it
into a graph report, a Markdown spec, pytest cases that run against the system under test,
and, through the model client, a page object and a test module. `test_data/trace_to_fixture.py`
turns a recorded Playwright trace into a seed fixture.

The sample input is the demo's lifecycle file, `demo_sut/config/subscription.machine.yaml`
(served by the demo at `GET /api/machines/subscription`). It has one deliberately unreachable
state, `suspended`, so every reachability feature below has a real case to report.

## Workflow model

**What it does.** `generators.workflow_model.load(path_or_mapping)` parses the neutral
workflow YAML into frozen dataclasses: `WorkflowDefinition` (key, title, version,
`initial_states`, `states`, `actions`, `fields`), `State` (title, transitions, `on_entry`),
`Transition` (`action`, `to`, `condition`, `automatic`), `Action` (title, condition,
operations) and `FieldSpec` (type, required, pattern, choices, ref, min, max, max_len).
Unknown keys are ignored at every level. A transition without an `action` (or with
`automatic: true`) is an automatic transition. `load_dir(directory)` loads every workflow
file under a directory and skips YAML files that are not workflows.

`WorkflowDefinition.problems()` lists what does not hang together (an initial state that is
not declared, a transition to an undeclared state, an undeclared action). Loading never
fails on those, so a test can report them as findings.

**How to use it.**

```python
from generators.workflow_model import load

d = load("demo_sut/config/subscription.machine.yaml")
d.states["draft"].actions  # ('submit', 'activate')
d.problems()  # []
```

**Configuration.** None. `WorkflowError` is raised only for input that is not a workflow at
all (not a mapping, no `key`, no `states`) or a file that cannot be read or parsed.

## State graph analysis

**What it does.** `generators.state_graph.StateGraph(definition)` builds the adjacency graph.
`paths(max_depth=20)` enumerates every path from each initial state with a cycle-safe
depth-first walk: a path ends at a terminal state, at the depth cap (`truncated`), or on the
edge that would revisit a state already on the path (`closes_cycle`, the edge is kept so it
is covered too). The first path is flagged `happy`. `unreachable()` lists states no initial
state reaches (breadth-first), `uncovered(paths)` lists transitions of reachable states no
path walks, and `ascii()` renders the graph as text for a report attachment. Each
`TraversalPath` has `name` (the state chain), `slug` (states and actions, unique),
`describe()`, `entry_state`, `terminal_state`, `actions` and `conditions`.

**How to use it.**

```bash
python -m generators.cli graph demo_sut/config/subscription.machine.yaml
# ... [suspended] --resume--> [active]
# unreachable: suspended
# paths: 44
```

**Configuration.** `max_depth` (default 20). The `state-machine-exhaustive` plugin reports
declared graphs (key, states, unreachable states, path count, problems) when its context
carries `workflow_files`: `plugin.execute({"source_dir": ".", "workflow_files": [...]})`.

## Spec writer

**What it does.** `generators.spec_writer.render_spec(definition)` renders a spec in the
`specs/` format: front matter, per-state actions with their conditions, targets and
operations, automatic transitions, the field schema (type, required, pattern, choices,
reference, bounds), reachability with the ASCII graph, and suggested `## Scenario` sections
(create, one per reachable state and action, field validation, happy path), each with
Preconditions, Steps, Expected and Tags. The output is deterministic, so a committed spec can
be compared with a fresh rendering: `specs/subscription.md` is checked that way by
`tests/generators/test_spec_writer.py`.

**How to use it.**

```bash
python -m generators.cli spec demo_sut/config/subscription.machine.yaml --out specs
```

**Configuration.** `--out` (default `specs`). The file is `<out>/<key>.md`.

## Generator pipeline and command line

**What it does.** `python -m generators.cli generate <workflow.yaml>` writes the spec, sends it
to `TestGenerator.generate_from_workflow` (in `core/ai/test_generator.py`), and writes
`ui/pages/<key>_page.py` and `ui/tests/test_<key>_generated.py`. The completion must carry
both blocks (`===PAGE_OBJECT_START===`/`===TEST_START===` markers, read by
`split_sections`). The wait is bounded and prints a progress line while it runs. Nothing is
written unless the whole generation succeeded: a missing API key, an API error, a timeout or
an incomplete completion leaves every target file as it was. Every subcommand prints the next
steps. The `/generate-from-workflow` slash command walks through the same flow.

**How to use it.**

```bash
python -m generators.cli generate demo_sut/config/subscription.machine.yaml --timeout 300
```

**Configuration.**

| Setting | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | required for `generate` and `convert`; without it nothing is generated |
| `GENERATOR_TIMEOUT_S` / `--timeout` | `420` | longest wait for one generation |
| `GENERATOR_PROGRESS_S` | `30` | seconds between progress lines |
| `--spec-dir`, `--page-dir`, `--test-dir` | `specs`, `ui/pages`, `ui/tests` | output folders |

## Recording conversion

**What it does.** `python -m generators.cli convert <recording> --name <name>` (or
`generators.cli.convert_recording(...)`) sends a `playwright codegen` script (Python or
TypeScript) to `TestGenerator.from_recording`. The prompt carries the project's selector
priority (`core.locator_strategy.priority_prompt()`). The completion must carry a page object,
a test and a JSON locator registry. Every registry entry is checked with
`core.locator_strategy.shape_error`, and one entry that cannot be rendered fails the whole
conversion. On success it writes `ui/pages/<name>_page.py`, `ui/tests/test_<name>.py` and
`ui/locators/<name>_locators.json`; on any failure it writes nothing.

**How to use it.**

```bash
playwright codegen --target python -o recordings/checkout.py http://127.0.0.1:8765/ui
python -m generators.cli convert recordings/checkout.py --name checkout
```

**Configuration.** As for `generate`, plus `--locator-dir` (default `ui/locators`).

## Config-driven cases

**What it does.** `generators.config_suite.generate_cases(definition)` returns pytest
parameters, one `Case` each:

| Kind | Checks |
|---|---|
| `config_key` | the key is not blank |
| `initial_reachable` | `problems()` is empty, and the system serves the same machine with the same initial states (skips with a reason when it does not serve it) |
| `declared_actions` | a non-terminal state offers an action, and every offered action is declared |
| `action_renders` | with the entity in the state, `[data-action="<name>"]` is on screen. A missing action FAILS; a known gap is passed as `known_missing` and becomes a strict xfail |

`run_case(case, adapter)` runs one. The system under test is reached through a
`MachineAdapter` you implement (`fetch_machine`, `open_in_state`, `rendered_actions`,
`start`, `drive`, `shows_state`). Checks run inside Allure steps, and `attach_graph`
attaches the ASCII graph. `with_marks(params, ADAPTER_KINDS, [pytest.mark.demo_ui])` marks
the cases that need screens, so the definition-only ones still run without a browser.

**How to use it.**

```python
CASES = generate_cases(load(MACHINE), known_missing={("active", "renew"): "BUG-7"})


@pytest.mark.parametrize("case", CASES)
def test_config(case, my_adapter):
    run_case(case, my_adapter)
```

## Traversal-path cases

**What it does.** `path_cases(definition, feasible=None, cover_only=False)` turns traversal
paths into pytest parameters named by states and actions
(`subscription:path:draft-submit->pending_payment-confirm->active...`). A path case drives
each transition through the adapter and checks `[data-state="<target>"]` after it; a wrong
state fails and names the step. `feasible` drops paths the system cannot walk (conditions
that refer to an earlier action are not something a graph walk can evaluate).
`cover_only=True` keeps a greedy covering set that still walks every transition.
`unreachable_cases(definition, known_defects)` gives one `state_reachable` case per state.
An unreachable state is a real failing case; a known one carries `known_defect` (keyed to the
reachability message), so it reads as the open defect and goes red when the state becomes
reachable.

**How to use it.**

```python
PATHS = path_cases(d, feasible=my_feasible, cover_only=True)
REACH = unreachable_cases(d, {"suspended": "BUG-101"})
```

## Zero-code workflow test factory

**What it does.** `generators.yaml_suite` expands registered workflow files into every case
above (render, reachability, traversal paths) through `pytest_generate_tests`. Adding an
entity is one line.

**How to use it.**

```python
from generators.yaml_suite import pytest_generate_tests  # noqa: F401

WORKFLOW_FILES = ["demo_sut/config/subscription.machine.yaml"]
WORKFLOW_KNOWN_DEFECTS = {"subscription": {"suspended": "BUG-101"}}
WORKFLOW_ADAPTER_MARKS = [pytest.mark.demo_ui]


def test_generated(yaml_case, my_adapter):
    run_case(yaml_case, my_adapter)
```

**Configuration.** Module attributes `WORKFLOW_FILES`, `WORKFLOW_KNOWN_MISSING`,
`WORKFLOW_KNOWN_DEFECTS`, `WORKFLOW_FEASIBLE` (each keyed by machine key),
`WORKFLOW_ALL_PATHS` (default: covering set only) and `WORKFLOW_ADAPTER_MARKS`. Without
`WORKFLOW_FILES` the files come from `WORKFLOW_SUITE_FILES` (paths separated by
`os.pathsep`).

## Trace to seed fixture

**What it does.** `python -m test_data.trace_to_fixture <trace.zip|dir> --out fixture.json`
reads a Playwright trace (zip or unpacked directory) and writes a
`test_data.fixture_replay` fixture. It:

* keeps the writes whose path matches a pattern, resolving bodies from `resources/<sha1>`;
* replaces the version path segment and a `version` query parameter with `${version}`, and
  named date fields with `${TODAY_TOKEN}`;
* captures the id of each created entity (a successful POST answering an `id`) as
  `${<collection>_id}` and uses the placeholder wherever that id is used later (path segments
  and id-like body keys only);
* drops abandoned entities (created, never used, not the last create).

It reports the step count, the terminus (the last call and the status it left), and whether
coverage is partial (a kept call that failed, or a body missing from the trace).

**How to use it.**

```bash
python -m test_data.trace_to_fixture reports/traces/subscribe.zip --out fixtures/subscribe.json \
    --date-field start_date --date-field effective_date
# steps: 4
# terminus: POST /api/subscriptions/${subscriptions_id}/actions/submit -> pending_payment
# coverage: complete
```

**Configuration.** `--method` (repeatable, default POST and PUT), `--path-pattern` (default
`^/api/`), `--version-segment` (default `/versions/\d+`), `--date-field` (repeatable),
`--status-field` (default `status`), `--name`. Exit code 1 when nothing matched.

Examples against the demo service: [demo-examples.md](demo-examples.md).
