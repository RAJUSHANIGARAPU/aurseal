# Search

Full-text search over everything the results store knows: test cases, failures,
documentation sections and defect links. It uses SQLite FTS5 (part of the standard
library's SQLite), so there is no search server. The code lives in `results/search.py`.

## What is indexed

| Type | One row per | Searchable text |
|---|---|---|
| `case` | catalogue case | title, key, description, steps, markers, links |
| `failure` | (test, failure fingerprint) — only the latest occurrence | message, fingerprint, node id, case title |
| `doc` | `##` section of a page in `docs/features/` | heading and section text |
| `defect` | defect id linked to a case | the titles of the cases it hangs on |

## Query syntax

Plain words are ANDed; `"quoted phrases"` match as phrases; a trailing `*` is a prefix
match. Filters are taken out before the text search:

| Filter | Example | Meaning |
|---|---|---|
| `type:` | `type:failure` | `case`, `failure`, `doc` or `defect` |
| `outcome:` | `outcome:error` | failure rows with that outcome |
| `marker:` | `marker:smoke` | cases or failures carrying that marker |
| `owner:` | `owner:team-pay` | the case owner |
| `since:` | `since:7d` | rows recorded in the last 7 days (`h`, `m` also work) |

Every user term is quoted before it reaches FTS5, so no input can produce a syntax
error. Results are ranked by `bm25()`, with recent failures boosted.

## Indexing

The index is updated explicitly, never by triggers, so a migration can never leave it
half-built:

- at the end of every recorded pytest run and every bundle import;
- by `python -m results search --reindex [--full]`.

An incremental reindex re-syncs cases, docs and defects and adds the failures of the
runs it has not indexed yet (tracked per run, so a bundle imported late is still picked
up, and an older occurrence never replaces a newer one). `--full` rebuilds everything.

## Without FTS5

If Python's SQLite was built without FTS5, migration `core/0002` runs its variant
`0002_search.nofts.sql` (a plain table) and the same API scans with `LIKE`. Every
response carries `engine: "fts5"` or `engine: "like"`, so the weaker mode is visible.

## How to use

```bash
python -m results search "gateway timeout type:failure since:7d"
python -m results search --reindex --full
python -m results search --export reports/dashboard/search-index.json
python -m results search "invoice" --format json
```

REST: `GET /search?q=&limit=&offset=` returns `hits` (`doc_type`, `doc_ref`, `title`,
`snippet`, `score`, `href`) and `engine`.

Python: `search(store, query, *, limit=50, offset=0)`, `reindex(store, *, full=False,
docs_dir=Path("docs/features"))`, `parse_query(q)`, `fts5_available(conn)`,
`export_index(store, out)`.

## Dashboard

The static dashboard embeds the same rows in `search.html` (and writes
`search-index.json` next to it), capped at 5 MB with the oldest failures dropped first;
the page searches them in the browser with no server.

## Configuration

| Option | Default | Meaning |
|---|---|---|
| `--docs-dir` | `docs/features` | Markdown pages indexed as `doc` rows |
| `RESULTS_DB` | `reports/results/results.db` | the store searched |

## Limits

FTS5 is per database. If the store moves to a server database, search needs that
engine's own full-text variant behind the same `search()` function. Pruned runs leave
their failure rows until the next `--full` reindex.
