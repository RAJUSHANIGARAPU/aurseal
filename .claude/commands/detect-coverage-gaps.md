---
description: "Use to see what is specified but not tested: OpenAPI operations, spec scenarios, requirements and workflow actions, with the same sources and matching as the CI coverage ratchets; writes the reports and offers to generate tests for the gaps."
---

# detect-coverage-gaps

## Usage

```
/detect-coverage-gaps [all | <openapi url or file> | <postman collection file>]
```

Default `all`. May be started by the user or by the assistant.

## Steps

1. Build the reports. The helper has no parser of its own: it runs the two CI ratchets
   report-only, with the sources of the CI steps "Coverage gaps (ratchet)" and "Workflow
   action gaps (ratchet)":

   ```bash
   python -m tools.slash.coverage_gaps "${TARGET:-all}" --out reports/gaps
   ```

   which is

   ```bash
   python -m traceability gaps --openapi demo_sut/openapi.json --specs specs \
     --requirements --baseline traceability/gap-baseline.json --report-only --out reports/gaps
   python -m traceability workflow-gaps --config-dir demo_sut/config \
     --baseline traceability/workflow-gap-baseline.json --report-only --out reports/gaps
   ```

   A URL or an OpenAPI file is added as one more `--openapi` input, a Postman collection
   as `--postman`. CI does not check those, so their items show up as new gaps.

   Matching, applied by the ratchets:
   - an **operation, scenario or requirement** is covered by an explicit
     `@pytest.mark.covers("METHOD /path")` / `covers("spec:<file>#<scenario>")` or a
     requirement link. Test names are not matched: a false "covered" is worse than a
     visible gap;
   - a **workflow action** (a `(state, action)` pair) is named when a test module holds
     the action as a string literal. A name is not an assertion.

2. Read `reports/gaps/gaps.md` (and `gaps.json`) and `reports/gaps/workflow-gaps.md`. The
   workflow report starts with `GAPS= ACTIONS= NEW= STALE=`; `NOT-MEASURED` there means the
   config dir was not bound, never zero gaps. Summarise: new gaps first, then the
   accepted ones per source.

3. If there are gaps, **ask** whether to generate tests: `/generate-from-prd <spec>` for
   spec gaps, `/generate-from-swagger <document>` for endpoint gaps. Generate only after an
   explicit yes, one gap source at a time. Without a yes, nothing is generated; the saved
   reports are the result.
