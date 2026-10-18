"""Results data layer: one SQLite store for runs, cases, results, evidence and verdicts.

Entry points:

* :func:`results.store.open_store` — the store (``ResultsStore`` protocol).
* :mod:`results.pytest_plugin` — the controller-only recorder.
* :mod:`results.bundle` — the NDJSON run bundle, the interchange format.
* ``python -m results`` — migrations, ingest, export, import, prune.

See ``docs/features/results-store.md``.
"""
