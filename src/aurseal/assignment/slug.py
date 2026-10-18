"""Project name -> directory slug and Python package name."""

from __future__ import annotations

import re
import sys
import unicodedata

MAX_LENGTH = 48
_SHADOWED = frozenset(sys.stdlib_module_names) | {
    "test", "tests", "conftest", "pytest", "playwright", "httpx", "pydantic", "requests",
    "src", "setup", "site",
}  # fmt: skip


def slugify(name: str) -> str:
    """``Café Checkout`` -> ``cafe-checkout``; ``2026 task`` -> ``qa-2026-task``.

    NFKD and ASCII fold, lower case, runs of other characters become one ``-``; must start
    with a letter (else ``qa-`` is prefixed); at most 48 characters, cut at a ``-``.
    Returns "" when nothing usable is left.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    if not slug:
        return ""
    if not slug[0].isalpha():
        slug = f"qa-{slug}"
    if len(slug) > MAX_LENGTH:
        cut = slug[: MAX_LENGTH + 1]
        slug = cut[: cut.rfind("-")] if "-" in cut[1:] else slug[:MAX_LENGTH]
    return slug.strip("-")


def package_name(slug: str) -> str:
    """The import name: ``-`` -> ``_``; a name that shadows a module gets ``_qa``."""
    name = slug.replace("-", "_")
    return f"{name}_qa" if name in _SHADOWED else name
