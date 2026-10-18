---
feature: living-documentation
---
# Living documentation

Feature pages stay hand-written prose, and each one shows, live, which tests prove it
and whether they passed. Nothing generated is committed: pages are rendered into
`reports/living-docs/`. The code lives in `results/livedocs.py`.

This page is itself a living page: rendered, the table below lists the tests in
`tests/results/` that carry `@pytest.mark.feature("living-documentation")`.

<!-- living:status -->

<!-- living:cases feature=living-documentation -->

## Front matter and directives

**What it does.** A page in `docs/features/` opts in with YAML front matter and HTML
comment directives (invisible when the raw Markdown is read):

```markdown
---
feature: billing
requirements: [REQ-12, REQ-14]
---
# Billing
<!-- living:status -->
<!-- living:cases feature=billing -->
<!-- living:cases requirement=REQ-12 -->
```

- `living:cases feature=<name>` / `requirement=<id>` becomes a table: case title, owner,
  latest outcome, last executed run and date, revision, evidence links.
- `living:status` becomes the page status line (added at the top when the page has none).

Tests link themselves with `@pytest.mark.feature("billing")` and the requirement marker;
both land in the store's `case_links`.

## Page status

| Status | When |
|---|---|
| failing | a linked case failed or errored in its latest sound run |
| unproven | no linked case; or not every linked case passed (skipped, not run, never executed); or the page's requirements carry coverage verdicts and none says `proved` |
| stale | everything passed, but the oldest of those latest executions is older than `LIVING_DOCS_STALE_DAYS` |
| proven | every linked case passed in its latest sound run, recently |

A page whose cases all skipped is never shown as passing. Runs the run-trust gate
judged FAIL are ignored.

## Lint: keeping docs and tests linked

`python -m results livedocs --check` fails (exit 1) on:

- a directive that references a feature or requirement no test case is linked to;
- a test case carrying `feature(x)` when no page declares `x` (front matter `feature:` /
  `features:` or a directive) — an orphan;
- malformed front matter or an unknown directive.

It needs no database: it collects the catalogue (or reads `--catalogue catalogue.json`).
The orphan check covers one direction only: a page that declares a feature without a
directive is not flagged.

## How to use

```bash
python -m results livedocs --check                       # CI guard, no DB
python -m results livedocs --out reports/living-docs/    # render (reads the results store)
open reports/living-docs/index.html
```

Output: `<page>.md` and `<page>.html` per living page, `index.md` / `index.html` with every
page's status, and `status.json`.

REST: `GET /docs` (pages with status), `GET /docs/{page}` (rendered Markdown plus status),
`GET /features/{feature}/cases`.

Python: `parse_page(path)`, `render_page(store, page, fmt="md"|"html")`,
`page_status(store, page)`, `check(docs_dir, catalogue)`, `build(store, docs_dir, out_dir)`.

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `LIVING_DOCS_STALE_DAYS` | 14 | age after which a passing page is `stale` |
| `--docs-dir` | `docs/features` | pages to read |
| `--out` | `reports/living-docs` | rendered output |

## Limits

A page proves only what is linked to it: a missing `feature` marker shows as
"unproven", never as a false green, but nothing forces authors to add the link.
