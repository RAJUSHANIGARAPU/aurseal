"""
Typing a date into a locale-formatted date field, keystroke by keystroke.

Datepicker inputs (Material and most others) parse the text as it is typed and
update their model per key event. ``fill()`` sets the DOM value in one go, the
model never hears about it, and the form submits the old date or none. So the date
is typed in the field's display format, the field is blurred, and an open calendar
popup is dismissed with Escape.

Then the field is read back: a date the picker rejected is cleared or reformatted,
and that has to fail here rather than as a wrong date three screens later.
"""

from __future__ import annotations

from datetime import date

from ui.components.value_entry import ValueNotLandedError

LOCALE_FORMATS: dict[str, str] = {
    "iso": "%Y-%m-%d",
    "en_US": "%m/%d/%Y",
    "en_GB": "%d/%m/%Y",
    "nl_NL": "%d-%m-%Y",
    "de_DE": "%d.%m.%Y",
    "fr_FR": "%d/%m/%Y",
}


def format_date(value: date, locale: str = "iso") -> str:
    try:
        return value.strftime(LOCALE_FORMATS[locale])
    except KeyError:
        raise ValueError(
            f"Unknown date locale {locale!r}; known: {sorted(LOCALE_FORMATS)}"
        ) from None


def type_date(
    field, value: date, locale: str = "iso", *, key_delay_ms: int = 60, verify: bool = True
) -> str:
    """Type ``value`` into ``field`` in ``locale``'s format and verify it stuck."""
    text = format_date(value, locale)
    field.focus()
    field.press("ControlOrMeta+A")
    field.press_sequentially(text, delay=key_delay_ms)
    field.press("Tab")
    field.page.keyboard.press("Escape")
    landed = field.input_value()
    if verify and landed.strip() != text:
        raise ValueNotLandedError(
            f"date field did not take {text!r} ({locale}); it holds {landed!r}. "
            "The picker may expect another format, or rejected the date."
        )
    return landed


def set_native_date(field, value: date) -> str:
    """
    Set a native ``<input type="date">`` and verify it holds ``value``.

    A native date input has no text model to type into: its segments follow the
    browser's locale and the keystroke path above lands digits in the wrong
    segment. Its value is always ISO (``YYYY-MM-DD``) whatever it displays, so it
    is set as ISO (``fill`` dispatches ``input`` and ``change``) and read back.
    """
    text = value.isoformat()
    field.fill(text)
    landed = field.input_value()
    if landed != text:
        raise ValueNotLandedError(f"date input did not take {text!r}; it holds {landed!r}")
    return landed
