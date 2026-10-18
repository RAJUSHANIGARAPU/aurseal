- **Locally:** each profile writes `reports/k6-<profile>-summary.html` (open it in a browser)
  and `reports/k6-<profile>-summary.json` (every metric k6 collected), and prints a short
  verdict: each SLO as `met` or `BREACHED`.
- **Exit code:** k6 exits non-zero when any threshold is breached; that is the verdict
  `make` and CI act on.
{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes the latest smoke summary.
{% endif -%}
- **A failed run:** start with the SLO table, then the checks table: it names the path
  that misbehaved.
