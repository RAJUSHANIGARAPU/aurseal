"""
Matching a test-id attribute *exactly* to a field path, not merely containing it.

Apps that stamp a schema path into ``data-testid`` (``form.customer.email``) tend
to stamp it on the field's helpers too: ``form.customer.email-error``,
``form.customer.email-label``. A substring match (``[data-testid*="customer.email"]``)
resolved with ``.first`` takes whichever comes first in the document — and when
the field is empty, that is often the error node, whose text is then read back as
the field's value. Every non-blank check downstream passes.

A value identifies a path only if it **is** the path or **ends** with it at a
segment boundary (``.`` or ``-``). Anything after the path — ``-error``, a deeper
segment — means the element is *about* the field rather than being it.

``identifies`` is the pure rule; ``path_test_id_pattern`` turns it into a pattern
for ``page.get_by_test_id(...)``, so the exactness rides on the preferred locator
strategy instead of a hand-written CSS attribute selector.
"""

from __future__ import annotations

import re


def identifies(attribute_value: str | None, path: str | None) -> bool:
    """True when ``attribute_value`` names exactly ``path`` (optionally prefixed)."""
    if not attribute_value or not path:
        return False
    return (
        attribute_value == path
        or attribute_value.endswith("." + path)
        or attribute_value.endswith("-" + path)
    )


def path_test_id_pattern(path: str) -> re.Pattern[str]:
    """A pattern equivalent to ``identifies(value, path)``, for ``get_by_test_id``."""
    if not path:
        raise ValueError("path must not be empty")
    return re.compile(r"(?:^|.*[.-])" + re.escape(path) + r"$")


def by_path(page, path: str):
    """The element whose test id identifies ``path`` exactly."""
    return page.get_by_test_id(path_test_id_pattern(path))
