{% if report in ['html', 'both'] -%}
- **Locally:** `reports/report.html`, one self-contained file.
{% endif -%}
{% if report in ['allure', 'both'] -%}
- **Allure:** `make report` renders `reports/allure-results` to `reports/allure-report/` (needs Java).
{% endif -%}
{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes the latest report.
{% endif -%}
- **A failed test** leaves a Playwright trace, a screenshot and a video in `reports/test-results/`.
  Open the trace with `uv run playwright show-trace reports/test-results/<test>/trace.zip`: it
  replays every action with the DOM, network and console at that moment.
