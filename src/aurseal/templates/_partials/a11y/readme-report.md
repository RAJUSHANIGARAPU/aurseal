{% if report in ['html', 'both'] -%}
- **Locally:** `reports/report.html`, one self-contained file.
{% endif -%}
{% if report in ['allure', 'both'] -%}
- **Allure:** `make report` renders `reports/allure-results` to `reports/allure-report/` (needs Java).
{% endif -%}
{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes the latest report.
{% endif -%}
- **Every scan** writes its findings to `reports/artifacts/<test>/axe.json`, and a failed scan
  prints them as a table: rule, impact, element, and the axe help link for the fix.
- **A failed test** also leaves a Playwright trace and a screenshot in `reports/test-results/`.
