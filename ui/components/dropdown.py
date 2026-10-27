"""
Choosing an option in a select / autocomplete widget and verifying the choice.

Two failure modes this guards against, both observed with Material-style widgets:

* **The wrong field.** Locating "the field whose label contains X" is a substring
  match resolved in document order: a label like "Term" also matches "Payment term"
  and "Term amount (read-only)", and only declaration order keeps the write on the
  right one. Asking for the **combobox role** with an **exact** accessible name
  means only a field that actually carries a dropdown, with exactly that label,
  can match — and more than one match is an error, not a coin toss.
* **The click that did not take.** Floating labels overlay the trigger and swallow
  pointer events, and an option list that re-renders mid-click selects nothing.
  The trigger is opened with a dispatched click (no hit-testing), the option is
  picked by role and name, and the trigger's text is **read back**; on a mismatch
  the popup is closed with Escape and the whole thing is retried.

And one choice rule for change tests, :func:`choose_other`: the target value is
drawn from what the control offers and must differ from the current value. A
fixed target that happens to equal the current value "changes" nothing and
passes every assertion about the change.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from core.logger import logger
from ui.components.wizard import settle

DEFAULT_ATTEMPTS = 3


class DropdownError(AssertionError):
    pass


class NothingToChange(LookupError):
    """The control offers no value different from the current one; the change cannot be made."""


def choose_other(
    current: str | None,
    preferred: str | None,
    offered: Iterable[str],
    same: Callable[[str | None, str], bool] = lambda a, b: a == b,
) -> str:
    """A value to change to: ``preferred`` when offered and different, else the first offered
    value that differs from ``current``.

    ``same`` decides equality (pass a normalising comparison for labels that
    differ only in case or spacing). Raises :class:`NothingToChange` when every
    offered value equals ``current``; a test should skip that candidate rather
    than submit a change that changes nothing.
    """
    options = [o for o in offered if o is not None]
    if preferred is not None and preferred in options and not same(current, preferred):
        return preferred
    for option in options:
        if not same(current, option):
            return option
    raise NothingToChange(
        f"no offered value differs from the current {current!r}; offered: {options}"
    )


def combobox_by_label(page, label: str | re.Pattern[str]):
    """The single combobox with exactly this accessible name; raises when 0 or >1 match."""
    box = (
        page.get_by_role("combobox", name=label, exact=True)
        if isinstance(label, str)
        else (page.get_by_role("combobox", name=label))
    )
    count = box.count()
    if count != 1:
        raise DropdownError(
            f"expected exactly one dropdown labelled {label!r}, found {count}. "
            "Use the exact visible label, or locate the field by test id."
        )
    return box


def select_option(
    page, trigger, option: str, *, attempts: int = DEFAULT_ATTEMPTS, option_timeout_ms: int = 6000
) -> str:
    """
    Open ``trigger``, choose the option named ``option`` and verify the trigger
    shows it. Returns the trigger's final text.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            trigger.dispatch_event("click")
            choice = page.get_by_role("option", name=option, exact=True).first
            choice.wait_for(timeout=option_timeout_ms)
            choice.click()
            shown = _shown_value(trigger)
            if option in shown:
                return shown
            last_error = DropdownError(f"picked {option!r} but the field shows {shown!r}")
        except Exception as exc:  # noqa: BLE001 - retried, then reported with context
            last_error = exc
        logger.warning(
            "[dropdown] attempt %d/%d for %r failed: %s", attempt, attempts, option, last_error
        )
        page.keyboard.press("Escape")
        settle(page, 5000)
    available = _visible_options(page)
    raise DropdownError(
        f"could not select {option!r} after {attempts} attempts ({last_error}). "
        f"Options seen: {available}"
    )


def select_by_label(page, label: str, option: str, **kwargs) -> str:
    return select_option(page, combobox_by_label(page, label), option, **kwargs)


def _shown_value(trigger) -> str:
    try:
        value = trigger.input_value()
    except Exception:  # noqa: BLE001 - a non-input trigger (a div with role=combobox)
        value = ""
    return (value or trigger.inner_text() or "").strip()


def _visible_options(page) -> list[str]:
    try:
        return page.get_by_role("option").all_inner_texts()
    except Exception:  # noqa: BLE001 - diagnostic only
        return []


def select_native(
    select, option: str, *, attempts: int = DEFAULT_ATTEMPTS, by: str = "label"
) -> str:
    """
    Choose ``option`` in a native ``<select>`` and read the choice back.

    A native select renders its options outside the page, so the open-and-click
    path above cannot reach them. ``by`` is ``"label"`` (the visible text) or
    ``"value"``. The selected option's visible text is read back after every
    attempt; a mismatch is retried and then raised with the options on offer.
    """
    if by not in ("label", "value"):
        raise ValueError("by must be 'label' or 'value'")
    shown = ""
    for attempt in range(1, attempts + 1):
        try:
            select.select_option(**{by: option})
        except Exception as exc:  # noqa: BLE001 - retried, then reported with context
            logger.warning(
                "[dropdown] native attempt %d/%d for %r: %s", attempt, attempts, option, exc
            )
            continue
        shown = _selected_text(select)
        landed = select.input_value() if by == "value" else shown
        if landed == option:
            return shown
        logger.warning("[dropdown] picked %r but the select shows %r", option, landed)
    raise DropdownError(
        f"could not select {option!r} after {attempts} attempts (shows {shown!r}). "
        f"Options seen: {_native_options(select)}"
    )


def _selected_text(select) -> str:
    try:
        text = select.evaluate("s => s.selectedIndex < 0 ? '' : s.options[s.selectedIndex].text")
        return (text or "").strip()
    except Exception:  # noqa: BLE001 - nothing selected reads as empty
        return ""


def _native_options(select) -> list[str]:
    try:
        return [t.strip() for t in select.get_by_role("option").all_inner_texts()]
    except Exception:  # noqa: BLE001 - diagnostic only
        return []
