---
description: "Turn written requirements (a requirements doc, user story or acceptance criteria; file, URL or inline text) into scenario specs plus UI and/or API tests that pass locally. Manual only."
disable-model-invocation: true
---

# generate-from-prd

## Usage

```
/generate-from-prd <file | URL | "inline requirement text"> [flow_name]
```

## Steps

1. **Ingest.** A file is read, a URL is fetched and reduced to plain text, inline text is
   used as given; all are capped at about 8,000 characters (the cut is announced):

   ```bash
   python -m tools.slash.prd ingest "$SOURCE"
   ```

2. **Too vague?** If `python -m tools.slash.prd vague "$SOURCE"` reports a reason, or the
   text does not say what a user does and what should happen, ask for clarification and
   stop. No files are written.

3. **Extract scenarios** in four groups: `main` (the success path), `validation` (input
   errors), `boundary` (edges and limits) and `prevented` (what must not be possible).
   Each scenario is precondition / action / expected outcome with tags (`ui`, `api`,
   `smoke`, `regression`). Every group gets at least one scenario, or a stated reason why
   it does not apply. Tests check the stated requirement, not the implementation.

4. **Write the spec first**, before any code - one file per flow in the existing
   `specs/` format (`specs/README.md`); these files are permanent and committed:

   ```bash
   python -m tools.slash.prd write --feature "$FLOW" --scenarios /tmp/scenarios.json
   ```

   The helper refuses a plan with a group neither covered nor marked not applicable.

5. **Classify and generate.** UI scenario -> page object + UI test module, following
   `/generate-from-ts` (locators in JSON, `BasePage.resolve`, no sleeps, data in a data
   file). API scenario -> API test module (+ client), following `/generate-from-swagger`.
   Mixed -> both.

6. **Compile** the new modules: `python -m compileall -q ui/pages ui/tests api/tests`.

7. **Run and fix** with the symptom tables of those two commands until 3 consecutive
   passes, at most 5 fix rounds (`python -m tools.slash.stability`, as in `/auto-run-fix`).

8. **Commit** specs, page objects, tests, locator and test-data files. If the run never
   reaches 3 consecutive passes, commit nothing beyond the specs.

## Rules

Where specific test users or data are needed but not available, leave a clearly marked
follow-up note in the spec (`follow-up` tag) and in the test (a skip naming what is
missing), rather than inventing them.
