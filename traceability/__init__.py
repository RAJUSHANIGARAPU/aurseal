"""Traceability: requirements, links, matrix, plans, coverage gaps, review state, release verdict.

Every capability is a plain function over plain inputs (artifact files or the results
store), so the pytest plugin, ``python -m traceability``, CI steps and REST handlers are
thin callers. Product requirements live here; ``gate.requirement`` is about something else
(whether a suite or a verdict is required in a lane).
"""
