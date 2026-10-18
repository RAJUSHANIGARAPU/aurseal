"""
Typing a value into a field and proving it landed.

The read-back is not padding, it is the point. ``Control+A`` selects all on Linux
and Windows but moves the caret to the line start on macOS, so typing ``57.25``
into a field holding ``100.00`` produces ``57.25100.00`` — or, after the input
mask swallows the second separator, ``5725100.00``. The app then computes
correctly on a number a hundred thousand times too large, and the run reports a
pricing bug that is really a keystroke bug. ``ControlOrMeta+A`` fixes the key on
every platform; comparing the landed value fixes the *class*: a wrong value stops
the test here, at the cause, instead of surfacing as wrong arithmetic screens later.

Amounts compare numerically, because a formatted field re-renders ``57.25`` as
``57,25`` or ``1,234.50`` once it blurs. ``parse_amount`` reads the common
notations; an unreadable value is never "equal".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from core.logger import logger

DEFAULT_KEY_DELAY_MS = 50


class ValueNotLandedError(AssertionError):
    """The field does not hold what was typed."""


def parse_amount(text: str | None) -> Decimal | None:
    """
    A number from a rendered amount, or None.

    Handles currency symbols and spaces, a leading minus or a trailing one,
    ``1,234.56`` and ``1.234,56`` (the last separator is the decimal one when both
    appear), ``57,25`` (one comma followed by 1-2 digits is decimal), and grouped
    integers such as ``1.234.567`` or ``1,234``. A lone separator followed by
    exactly three digits is read as thousands grouping, which is how such a value
    is rendered by every grouping locale.
    """
    if text is None:
        return None
    raw = re.sub(r"[\s\u00a0\u202f]", "", text)
    negative = raw.startswith("-") or raw.endswith("-") or raw.startswith("(")
    digits = re.sub(r"[^0-9.,]", "", raw)
    if not re.fullmatch(r"\d[\d.,]*", digits or ""):
        return None
    last_dot, last_comma = digits.rfind("."), digits.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_sep = "." if last_dot > last_comma else ","
    elif last_dot >= 0 or last_comma >= 0:
        sep = "." if last_dot >= 0 else ","
        tail = digits.rsplit(sep, 1)[1]
        grouped = digits.count(sep) > 1 or len(tail) == 3
        decimal_sep = None if grouped else sep
    else:
        decimal_sep = None
    if decimal_sep is None:
        normalized = digits.replace(".", "").replace(",", "")
    else:
        group_sep = "," if decimal_sep == "." else "."
        integer, _, fraction = digits.replace(group_sep, "").rpartition(decimal_sep)
        if decimal_sep in integer:
            return None
        normalized = f"{integer or '0'}.{fraction}"
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    return -value if negative else value


def amounts_equal(rendered: str | None, expected: str | None) -> bool:
    actual, wanted = parse_amount(rendered), parse_amount(expected)
    return actual is not None and wanted is not None and actual == wanted


def text_equal(rendered: str | None, expected: str | None) -> bool:
    return (rendered or "").strip() == (expected or "").strip()


def fill_and_verify(
    field,
    value: str,
    *,
    compare: Callable[[str | None, str | None], bool] = text_equal,
    key_delay_ms: int = DEFAULT_KEY_DELAY_MS,
    description: str = "field",
) -> str:
    """
    Replace the field's content with ``value`` keystroke by keystroke, blur it,
    and raise ``ValueNotLandedError`` unless ``compare(landed, value)``.

    Keystrokes rather than ``fill()``: input masks and formatters often listen to
    key events only, and ``fill()`` sets the value in one go behind their back.
    """
    field.scroll_into_view_if_needed()
    field.focus()
    field.press("ControlOrMeta+A")
    field.press_sequentially(value, delay=key_delay_ms)
    field.press("Tab")
    landed = field.input_value()
    if not compare(landed, value):
        raise ValueNotLandedError(
            f"{description} did not take the value: asked for {value!r}, it holds {landed!r}. "
            "Everything downstream would be computed from a value the app never received. "
            "A value that looks like the old and new ones run together means the clear did "
            "not take effect."
        )
    logger.info("%s set to %r (reads %r)", description, value, landed)
    return landed


def fill_amount_and_verify(field, amount: str, **kwargs) -> str:
    return fill_and_verify(field, amount, compare=amounts_equal, **kwargs)
