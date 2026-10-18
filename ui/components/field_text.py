"""
Is a string scraped from a form field a *value*, or an echo of the field's own label?

Reading a field by its visible text is a trap. A rendered form field contains its
floating label, and in most component libraries its validation message is a
*descendant* of the field, not a sibling. An **empty** field therefore reads back
as its label text, sometimes followed by "is required" — a plausible, non-blank string
that passes every "has a value" check. That is how a working product gets a bug
filed against it.

These are the string-level checks; the DOM-level fix (read ``input_value()``, not
the wrapper's text) is in ``field_reader``. Kept pure so they are testable offline.

* ``strip_label_echo`` removes every **word-bounded**, case-insensitive occurrence
  of the label. Word-bounded is load-bearing: stripping ``"Land"`` from
  ``"Netherland"`` must not eat the value. A boundary is required only on a side
  where the label begins/ends with a word character, so ``"Amount (€)"`` matches.
* ``carries_no_value`` is the predicate callers want: blank, a pure label echo, or
  nothing left but a validation message. Validation phrases are matched on wording
  (``DEFAULT_VALIDATION_PHRASES``, extend per application/locale), never on "looks
  like a sentence", so genuine free text is never rejected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

DEFAULT_VALIDATION_PHRASES: tuple[str, ...] = (
    "is required",
    "is invalid",
    "is not valid",
    "has an invalid value",
    "must be filled",
    "must not be empty",
    "cannot be empty",
    "please enter",
    "please select",
)
_TRIM = "\\s*:\u2022\\-\u2013"


def collapse(text: str) -> str:
    """Non-breaking spaces to spaces, whitespace runs to one space, trimmed."""
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _label_pattern(label: str) -> re.Pattern[str]:
    prefix = r"\b" if label[0].isalnum() else ""
    suffix = r"\b" if label[-1].isalnum() else ""
    return re.compile(prefix + re.escape(label) + suffix, re.IGNORECASE)


def strip_label_echo(scraped: str | None, label: str | None) -> str:
    """What is left of ``scraped`` once every occurrence of ``label`` is removed."""
    if scraped is None:
        return ""
    value = collapse(scraped)
    needle = collapse(label or "")
    if not needle:
        return value
    value = collapse(_label_pattern(needle).sub(" ", value))
    # A required marker and the punctuation a label drags along are not a value.
    return re.sub(f"^[{_TRIM}]+|[{_TRIM}]+$", "", value).strip()


def is_label_echo_only(scraped: str | None, label: str | None) -> bool:
    return strip_label_echo(scraped, label) == ""


def looks_like_validation_message(
    value: str | None, phrases: Iterable[str] = DEFAULT_VALIDATION_PHRASES
) -> bool:
    if not value:
        return False
    text = collapse(value).lower()
    return any(phrase.lower() in text for phrase in phrases)


def carries_no_value(
    scraped: str | None, label: str | None, phrases: Iterable[str] = DEFAULT_VALIDATION_PHRASES
) -> bool:
    """True when ``scraped`` holds nothing of the field's own: blank, label echo, or an error."""
    residue = strip_label_echo(scraped, label)
    return residue == "" or looks_like_validation_message(residue, phrases)
