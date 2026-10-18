"""
Reading what a form field holds, and whether the user could change it.

Resolution follows the framework's locator order: the **test id** first, matched
exactly against a field path (``exact_attribute``) so an ``-error`` or ``-label``
twin cannot answer for the field; then the **label**, exact, and *refusing* an
ambiguous match — two fields with the same label is a question for the caller, not
something to settle by document order.

The value is ``input_value()`` for form controls. Only a non-input element falls
back to its text, and that text goes through ``field_text.strip_label_echo`` so an
empty field reads as ``""`` and never as its own label. ``FieldValue.empty`` uses
``carries_no_value``, which also treats a leftover validation message as empty.

Read-only detection checks ``readonly``, ``aria-readonly`` and disabled; a field
that renders no input at all is static text and therefore read-only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.logger import logger
from ui.components.exact_attribute import by_path
from ui.components.field_text import DEFAULT_VALIDATION_PHRASES, carries_no_value, strip_label_echo


class AmbiguousFieldError(AssertionError):
    pass


@dataclass(frozen=True)
class FieldValue:
    raw: str
    via: str  # "test-id", "label" or "absent"
    label: str | None = None

    def empty(self, phrases: Iterable[str] = DEFAULT_VALIDATION_PHRASES) -> bool:
        return carries_no_value(self.raw, self.label, phrases)


def _present(locator) -> bool:
    try:
        return locator.count() > 0
    except Exception:  # noqa: BLE001 - an unresolvable locator is absent
        return False


def rendered_value(element, label: str | None = None) -> str:
    """``input_value()`` for a control; label-stripped text for anything else."""
    try:
        value = element.input_value()
        return (value or "").strip()
    except Exception:  # noqa: BLE001 - not a form control
        pass
    try:
        text = element.text_content() or ""
    except Exception:  # noqa: BLE001
        return ""
    return strip_label_echo(text, label)


def locate_field(page, *, path: str | None = None, label: str | None = None):
    """The field for ``path`` (test id) or else ``label``; None when neither is on screen."""
    if path:
        by_id = by_path(page, path)
        count = by_id.count()
        if count > 1:
            raise AmbiguousFieldError(f"{count} elements carry a test id identifying {path!r}")
        if count == 1:
            return by_id, "test-id"
    if label:
        by_label = page.get_by_label(label, exact=True)
        count = by_label.count()
        if count > 1:
            raise AmbiguousFieldError(
                f"{count} fields are labelled {label!r}; address it by test id"
            )
        if count == 1:
            return by_label, "label"
    return None, "absent"


def read_field(page, *, path: str | None = None, label: str | None = None) -> FieldValue:
    """What the field holds. ``via == "absent"`` when nothing matched."""
    if not path and not label:
        raise ValueError("read_field needs a path or a label")
    element, via = locate_field(page, path=path, label=label)
    if element is None:
        logger.warning("field %s not on screen", path or label)
        return FieldValue("", via, label)
    return FieldValue(rendered_value(element, label), via, label)


def is_read_only(element) -> bool:
    """True for a readonly, aria-readonly, disabled or non-input element."""
    try:
        if element.get_attribute("readonly") is not None:
            return True
        if (element.get_attribute("aria-readonly") or "").lower() == "true":
            return True
        if element.is_disabled():
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not determine editability (%s); reporting editable", exc)
        return False
    try:
        return not element.is_editable()
    except Exception as exc:  # noqa: BLE001
        # Playwright refuses is_editable() on an element that is no form control at all;
        # such a field is static text, which nobody can edit.
        try:
            element.input_value()
        except Exception:  # noqa: BLE001 - not a control: static text
            return True
        logger.warning("could not determine editability (%s); reporting editable", exc)
        return False


def field_is_read_only(page, *, path: str | None = None, label: str | None = None) -> bool | None:
    """Read-only state of a field, or None when it is not on screen (undetermined)."""
    element, _ = locate_field(page, path=path, label=label)
    if element is None or not _present(element):
        return None
    return is_read_only(element)
