# results-history

Machine-written branch: one gzip-compressed NDJSON run bundle per test run, under
`bundles/<YYYY-MM>/`. CI rebuilds the results database from these files with
`python -m results history rebuild --dir <checkout>`; the database is a cache, these
bundles are the record. Do not edit files by hand: a bundle is imported as-is.
