{% if report in ['html', 'both'] -%}
- **Locally:** `reports/report.html`, one self-contained file.
{% endif -%}
{% if report in ['allure', 'both'] -%}
- **Allure:** `make report` renders `reports/allure-results` to `reports/allure-report/` (needs Java).
{% endif -%}
{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes the latest report.
{% endif -%}
- **A failed live test** leaves a screenshot and the page source (the UI hierarchy as XML)
  in `reports/artifacts/<test>/`. The page source shows which accessibility ids the screen
  really had at that moment.
