{% if report in ['html', 'both'] -%}
- **Locally:** `reports/report.html`, one self-contained file.
{% endif -%}
{% if report in ['allure', 'both'] -%}
- **Allure:** `make report` renders `reports/allure-results` to `reports/allure-report/` (needs Java).
{% endif -%}
{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes the latest report.
{% endif -%}
- **A failed test** shows the captured log: every request with its status, credentials replaced
  by `***`. A GraphQL error lists the server's `errors[]` messages, and a schema drift failure
  prints the breaking changes first, then the full diff against `schema.graphql`.
