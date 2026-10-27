---
paths:
  - "tests/demo_examples/change/**"
  - "test_data/scenario_manifest.py"
---
# Change-scenario manifest

`tests/demo_examples/change/change-cases.json` lists every subscription change the demo
accepts in a change draft, grouped by the rule that decides it (`CHG-TERMS-n`,
`CHG-BILL-n`, `CHG-SETUP-n`, `CHG-OWNER-n`, `CHG-LOCK-n`, `CHG-LIFE-n`).

- Every case has an honest state: `automated` (names its `test`), `planned` (says what it
  is `waiting_on`) or `manual` (gives `why_manual`). Never mark a case automated before its
  test exists and runs.
- A case counts as proven only when it is automated, `seen_passing` and carries a `proof`.
- Adding a test for a case: name the case's `test`, keep `action`, `ends_in`, `reasons` and
  `touches` in step with what the test drives, and let `test_change_case_manifest.py` check
  it (it loads through `test_data.scenario_manifest.CaseManifest`).
- A change that makes a planned case possible moves it to automated in the same commit as
  its test; a removed test moves its case back, never silently.
