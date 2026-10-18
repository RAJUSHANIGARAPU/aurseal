"""Clean a scraped form-field value: strip the echoed label and recognise validation messages.

Reading a form field's text from the DOM often returns more than the value:
the floating label, a required marker, and any validation message rendered
inside the same container. An empty field then scrapes as
``"Legal form Legal form has an invalid value"`` — a plausible, non-blank
string for a field holding nothing.

* :func:`strip_label_echo` removes every case-insensitive, **word-bounded**
  occurrence of the label (a blind substring strip of ``"Land"`` would turn the
  value ``"Netherland"`` into ``"Nether"``), plus required-marker punctuation
  and non-breaking spaces.
* :func:`carries_no_value` is the predicate callers want: blank, a pure label
  echo, or nothing but a validation message.

Validation wording is product- and language-specific, so it is a parameter;
:data:`DEFAULT_VALIDATION_PHRASES` holds common English phrasings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

DEFAULT_VALIDATION_PHRASES: tuple[str, ...] = (
    "is required",
    "invalid value",
    "has an invalid",
    "must be filled",
    "may not be empty",
    "cannot be empty",
)

_MARKER = "\\s*:\u2022\\-\u2013"


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _label_pattern(label: str) -> re.Pattern[str]:
    prefix = r"\b" if label[0].isalnum() else ""
    suffix = r"\b" if label[-1].isalnum() else ""
    return re.compile(prefix + re.escape(label) + suffix, re.IGNORECASE)


def strip_label_echo(scraped: str | None, label: str | None) -> str:
    """The value left once every echo of ``label`` is removed; ``""`` when nothing survives."""
    if scraped is None:
        return ""
    value = _collapse(scraped)
    needle = _collapse(label or "")
    if not needle:
        return value
    value = _collapse(_label_pattern(needle).sub(" ", value))
    value = re.sub(f"^[{_MARKER}]+", "", value)
    value = re.sub(f"[{_MARKER}]+$", "", value)
    return value.strip()


def is_label_echo_only(scraped: str | None, label: str | None) -> bool:
    return strip_label_echo(scraped, label) == ""


def looks_like_validation_message(
    value: str | None, phrases: Iterable[str] = DEFAULT_VALIDATION_PHRASES
) -> bool:
    """Matched on specific wording, never on "looks like a sentence", so free text survives."""
    if value is None:
        return False
    v = _collapse(value).lower()
    return any(p.lower() in v for p in phrases)


def carries_no_value(
    scraped: str | None,
    label: str | None,
    phrases: Iterable[str] = DEFAULT_VALIDATION_PHRASES,
) -> bool:
    """True when the scrape holds no value of the field's own. A caller handed True read nothing."""
    residue = strip_label_echo(scraped, label)
    return residue == "" or looks_like_validation_message(residue, phrases)
