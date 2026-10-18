# Dashboards

A static HTML dashboard rendered from the results store: a directory of plain pages
with inline CSS, inline SVG charts and a few lines of vanilla JavaScript. No server, no
external fetches, no framework; it opens from disk and works as a CI artefact. The code
lives in `results/dashboard/`.

## Pages

| Page | Shows |
|---|---|
| `index.html` | last run headline (pass rate, executed of selected, integrity verdict), pass rate and executed share per run, lanes, recent runs |
| `runs/<run>.html` | run metadata, outcome counts, comparison with the previous run on the same lane and branch, failures with message, fingerprint and evidence links, every test |
| `cases/<case>.html` | case fields and steps, trend (pass rate, flips, streak, failing since, retry-masked passes, duration p50/p95 and drift), outcome timeline with revision boundaries, duration chart, history |
| `unstable.html` | cases that flip or pass only after a retry |
| `slowest.html` | cases by median duration |
| `catalogue.html` | the whole catalogue with a client-side filter |
| `search.html` | client-side search over the search index embedded in the page (built by the search feature) |

**Every number carries its denominator**, for example `92% (46/50 executed, 12 skipped)`.
A run or case that executed nothing reads `unmeasured (0 executed, …)`, and its chart point
is a hollow marker labelled *unmeasured*: the dashboard cannot show green over nothing.
Runs the integrity gate judged FAIL are marked, and left out of case windows.

**Evidence links** are relative to the artefact root (`RESULTS_ARTIFACT_ROOT`, default the
current directory), or prefixed with `--base-artifact-url` when the artefacts are hosted.
A link pointing anywhere else is shown as text, never as a link.

## How to use

```bash
python -m results dashboard --out reports/dashboard/ [--window 30] [--base-artifact-url URL]
open reports/dashboard/index.html
```

Python: `results.dashboard.render(store, out_dir, *, window=30, base_artifact_url=None)`
returns the files written. `results.dashboard.svg.line_chart(points, *, y_label,
denominators)` and `timeline(outcomes, boundaries)` produce the deterministic inline SVG.

## In CI

A `dashboard` job after the test jobs downloads their bundles, rebuilds the database
(including the `results-history` branch when it exists), renders, and uploads
`reports/dashboard/` as an artefact. It runs no pytest. See the integration note for
the job.

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `--window` | 30 | runs shown and used for case trends |
| `--base-artifact-url` | unset | prefix for evidence links |
| `RESULTS_ARTIFACT_ROOT` | current directory | where relative evidence URIs resolve |
| `RESULTS_DB` | `reports/results/results.db` | the store rendered |

## Other front ends

Rendering reads only the trends and catalogue modules and the store's named queries. A
Grafana board can point its SQLite (or a future Postgres) data source at the same
database and reuse the SQL in `results/queries/`.

## Limits

Static means no live view: the dashboard is as fresh as the last render. The charts are
basic on purpose; needing interactivity beyond filtering is the signal to move to a
served front end.
