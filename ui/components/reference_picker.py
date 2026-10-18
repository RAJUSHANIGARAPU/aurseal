"""
Picking a record in a "choose a reference" dialog.

Lookup dialogs (pick a customer, a supplier, an account) usually open on a short
default list and offer a search. Two habits make picking reliable:

* **Try the default list first.** Searching first is slower and often worse: a
  search on the wrong column over-filters to nothing, while the record was on
  screen all along. Only when the row is not in the default list is the search
  used.
* **Wait for the dialog to close.** The field is written when the dialog
  detaches; reading the field the moment after the click reads the old value.
  Dialogs can stack, so the wait is for the dialog *count* to drop below what it
  was before the click, not for "no dialog".

Everything is located by role (``dialog``, ``row``, ``searchbox``/``textbox``)
inside the topmost dialog, never by CSS.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from core.logger import logger
from ui.components.wizard import settle


class ReferencePickError(AssertionError):
    pass


def _dialogs(page):
    return page.get_by_role("dialog")


def _topmost(page):
    return _dialogs(page).last


def _row(page, text: str | re.Pattern[str]):
    return _topmost(page).get_by_role("row").filter(has_text=text).first


def _click_and_wait_closed(page, row, close_timeout_ms: int) -> None:
    open_before = _dialogs(page).count()
    row.click()
    if open_before == 0:
        return
    try:
        expect(_dialogs(page)).to_have_count(open_before - 1, timeout=close_timeout_ms)
    except Exception:  # noqa: BLE001 - the field read that follows will say what happened
        logger.warning(
            "[reference] dialog not closed %dms after clicking the row", close_timeout_ms
        )


def search(page, term: str) -> None:
    """Type ``term`` into the dialog's search box and submit it."""
    dialog = _topmost(page)
    box = dialog.get_by_role("searchbox")
    if box.count() == 0:
        box = dialog.get_by_role("textbox")
    box = box.first
    box.fill(term)
    box.press("Enter")
    settle(page, 6000)


def pick(
    page,
    text: str | re.Pattern[str],
    *,
    search_term: str | None = None,
    default_list_timeout_ms: int = 4000,
    row_timeout_ms: int = 10000,
    close_timeout_ms: int = 10000,
) -> str:
    """
    Click the row containing ``text``; returns ``"default-list"`` or ``"search"``
    depending on where it was found. Raises ``ReferencePickError`` with the visible
    rows when it is in neither.
    """
    row = _row(page, text)
    try:
        row.wait_for(timeout=default_list_timeout_ms)
        _click_and_wait_closed(page, row, close_timeout_ms)
        return "default-list"
    except Exception:  # noqa: BLE001 - not in the default list; search for it
        logger.info("[reference] %r not in the default list, searching", text)
    search(page, search_term or (text if isinstance(text, str) else text.pattern))
    row = _row(page, text)
    try:
        row.wait_for(timeout=row_timeout_ms)
    except Exception as exc:
        rows = _visible_rows(page)
        raise ReferencePickError(
            f"no row containing {text!r} after searching; rows: {rows}"
        ) from exc
    _click_and_wait_closed(page, row, close_timeout_ms)
    return "search"


def _visible_rows(page) -> list[str]:
    try:
        return _topmost(page).get_by_role("row").all_inner_texts()[:20]
    except Exception:  # noqa: BLE001 - diagnostic only
        return []
