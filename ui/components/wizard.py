"""
Submitting a step of a multi-step form, and letting the app settle around it.

Two lessons from long wizard flows:

* **Hidden copies of the submit button linger.** Earlier steps stay in the DOM,
  hidden, with the same accessible name, so ``.first`` resolves to a copy that can
  never be clicked and the click times out. Only the *visible* button is targeted.
  When it is not clickable, the visible buttons and alert texts are logged, which
  is almost always the actual answer (a validation message, a renamed button).
* **Settling must not throw.** Waiting for network idle is a best effort around a
  submit — an app with a polling request never reaches idle. ``settle`` returns
  whether idle was reached and logs when it was not, so slow-backend dead time
  leaves a trail instead of failing a step that went through.
"""

from __future__ import annotations

import re

from core.logger import logger


def settle(page, timeout_ms: int = 8000) -> bool:
    """Wait for network idle; True when reached, False (logged) when not."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 - see module docstring
        logger.warning("no network idle within %dms at %s; moving on", timeout_ms, page.url)
        return False


def visible_button(page, name: str | re.Pattern[str]):
    exact = isinstance(name, str)
    return page.get_by_role("button", name=name, exact=exact).filter(visible=True).first


def submit_step(
    page,
    button_name: str | re.Pattern[str],
    *,
    settle_before_ms: int = 8000,
    settle_after_ms: int = 12000,
) -> None:
    """Click the visible ``button_name`` between two settle waits."""
    settle(page, settle_before_ms)
    button = visible_button(page, button_name)
    try:
        button.click()
    except Exception:
        logger.warning(
            "'%s' cannot be clicked. Buttons on screen: %s | alerts: %s",
            button_name,
            _texts(page.get_by_role("button").filter(visible=True)),
            _texts(page.get_by_role("alert")),
        )
        raise
    settle(page, settle_after_ms)


def has_visible_alerts(page) -> bool:
    """True when any element with role=alert (validation summaries, error toasts) is visible."""
    return page.get_by_role("alert").filter(visible=True).count() > 0


def _texts(locator) -> list[str]:
    try:
        return locator.all_inner_texts()
    except Exception:  # noqa: BLE001 - diagnostic only
        return []
