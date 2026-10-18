---
description: "Use to see what is specified but not tested: scenario specs, OpenAPI documents and Postman collections against the UI and API test modules; writes a dated report and offers to generate tests for the gaps."
---

# detect-coverage-gaps

## Usage

```
/detect-coverage-gaps [all | specs/<file>.md | <url to an OpenAPI document>]
```

Default `all`. May be started by the user or by the assistant.

## Steps

1. Build the report (sources: `specs/*.md` without the format guide `README.md`, OpenAPI /
   Swagger JSON and Postman collections under `specs/`, `api/`, `demo_sut/`, `schemas/`;
   tests: `ui/tests/` and `api/tests/`):

   ```bash
   python -m tools.slash.coverage_gaps "${TARGET:-all}" --out reports
   ```

   Coverage rules, applied by the helper:
   - a **scenario** is covered when a test function name contains `test_` + the scenario name
     in snake_case (lowercase, non-alphanumeric runs to `_`, trimmed). `## Scenario: x` and
     `## Scenario x` are both scenario headings;
   - an **operation** is covered when an API test calls its path with the same method, or a
     test carries `@pytest.mark.covers("METHOD /path")`.

2. The report has the date, a summary table per source, and three sections: sources with
   **no tests at all** (highest priority), partially covered sources with the missing
   scenarios or endpoints, and fully covered sources. It is saved as
   `reports/coverage-gaps-<date>.md`.

3. If there are gaps, **ask** whether to generate tests: `/generate-from-prd <spec>` for
   spec gaps, `/generate-from-swagger <document>` for endpoint gaps. Generate only after an
   explicit yes, one gap source at a time. Without a yes, nothing is generated; the saved
   report is the result.

The CI step `python -m traceability gaps` is the separate ratchet over explicit `covers`
links; this command is the readable overview and also matches by test name.
