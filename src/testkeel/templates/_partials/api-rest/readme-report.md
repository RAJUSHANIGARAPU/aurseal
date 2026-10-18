{% if report in ['html', 'both'] -%}
- **Locally:** `reports/report.html`, one self-contained file.
{% endif -%}
{% if report in ['allure', 'both'] -%}
- **Allure:** `make report` renders `reports/allure-results` to `reports/allure-report/` (needs Java).
{% endif -%}
{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes the latest report.
{% endif -%}
- **A failed test** shows the captured log: every request and response line, with
  credentials replaced by `***`.
