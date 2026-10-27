---
paths:
  - ".github/workflows/**"
  - "gate/lanes.py"
  - "gate/workflow_lint.py"
  - "gate/run_inputs.py"
---
# CI time budget and how a test graduates

- `tests/test_ci_selection.py` fails when a collected test is selected by no pytest command
  in `tests.yml`. Wire a new test directory or marker in the same change; never widen the
  gate to tolerate the gap.
- The critical path of `tests.yml` (summed `timeout-minutes` along `needs`) stays within 60
  minutes; `python -m gate.workflow_lint` names it. Raise a timeout only with that in view.
- Every job has `timeout-minutes`; every upload has `retention-days` (heavy evidence at most
  7). Pin actions to exact versions.
- A new live test starts opt-in (its lane marker, excluded from the gate expression in
  `gate.lanes`), gets one watched run on a `bringup/<test path>` branch, and only then joins
  the gate. An unwatched test must not be able to redden the published build.
- `cancel-in-progress` stays false on the default branch; a dispatch-triggered workflow
  gets its own concurrency group (`post-deploy-<env>`), never the gate's.
- Run `python -m gate.workflow_lint` before committing a workflow (the pre-commit hook does).
