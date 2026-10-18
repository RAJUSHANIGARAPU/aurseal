- **Locally:** each profile writes `reports/<profile>.html` (Locust's report: charts,
  percentiles, failures) and `reports/<profile>_stats.csv`, which the SLO check reads.
- **Verdict:** the SLO check prints each budget as met or `BREACHED` and exits non-zero on
  a breach; that is what `make` and CI act on.
{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes the latest smoke report.
{% endif -%}
- **A failed run:** start with the SLO check's output, then the Failures tab of the HTML
  report: it names the request and the status it got.
