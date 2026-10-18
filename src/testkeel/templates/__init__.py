"""Copier templates for generated assignment projects.

A project is two Copier passes into one directory: the common skeleton (``_base``),
then the type overlay, which wins where both ship a file. ``render`` is the only
entry point; callers pass answers as data and never prompt.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).resolve().parent

#: Types with a template directory. Order is the order ``list-types`` shows.
TYPES: tuple[str, ...] = (
    "web-ui",
    "api-rest",
    "api-graphql",
    "a11y",
    "strategy",
    "perf-k6",
)

#: Answers a caller may pass. Everything else is computed or has a default.
DATA_KEYS: frozenset[str] = frozenset(
    {
        "project_name",
        "project_slug",
        "company",
        "task_summary",
        "target_url",
        "api_base_url",
        "python_version",
        "report",
        "docker",
        "ci",
        "credit",
        "updatable",
    }
)


def render(type_: str, destination: Path, data: Mapping[str, Any]) -> Path:
    """Render ``type_`` into ``destination`` (base, then overlay) and return it.

    Raises ``ValueError`` for an unknown type or an unknown answer, so a typo in a
    caller fails here instead of silently falling back to a default.
    """
    if type_ not in TYPES:
        raise ValueError(f"unknown assignment type {type_!r}; known: {', '.join(TYPES)}")
    unknown = sorted(set(data) - DATA_KEYS)
    if unknown:
        raise ValueError(f"unknown template answers: {', '.join(unknown)}")

    from copier import run_copy  # optional dependency, imported only when rendering

    answers = {**data, "type": type_}
    for layer in ("base", "overlay"):
        run_copy(
            str(TEMPLATES_DIR),
            str(destination),
            data={**answers, "layer": layer},
            defaults=True,
            overwrite=True,
            quiet=True,
        )
    return destination
