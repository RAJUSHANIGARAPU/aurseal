"""
A screenshot inside every ``allure.step``, opt-in.

With ``ALLURE_STEP_SCREENSHOTS=true`` each step gets a viewport PNG of the page as
the step *ends*, attached while the step is still open, so in the report the image
sits inside the step it shows. This is the cheapest way to see where a long flow
went wrong without opening a trace.

It hooks Allure's own ``stop_step`` with ``tryfirst`` so it runs before
allure-pytest closes the step. The page comes from ``set_current_page`` (a fixture
calls it); API-only tests never set one and the hook is a no-op for them. A
failed capture is logged at DEBUG and never affects the step or the test.
"""

from __future__ import annotations

import contextvars
import os

import allure_commons

from core.logger import logger

ENABLED_VARIABLE = "ALLURE_STEP_SCREENSHOTS"
_current_page: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "step_screenshot_page", default=None
)


def set_current_page(page) -> None:
    """Make ``page`` the one steps are captured from (``None`` to stop)."""
    _current_page.set(page)


def enabled(environ: dict[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get(ENABLED_VARIABLE, "").strip().lower() in ("1", "true", "yes", "on")


class StepScreenshotListener:
    def __init__(self, attach=None) -> None:
        self._titles: dict[str, str] = {}
        self._attach = attach

    @allure_commons.hookimpl(tryfirst=True)
    def start_step(self, uuid, title, params):
        self._titles[uuid] = title

    @allure_commons.hookimpl(tryfirst=True)
    def stop_step(self, uuid, exc_type, exc_val, exc_tb):
        title = self._titles.pop(uuid, None) or "step"
        page = _current_page.get()
        if page is None:
            return
        try:
            png = page.screenshot()
        except Exception as exc:  # noqa: BLE001 - never affects the step
            logger.debug("[step-screenshot] no capture for '%s': %s", title, exc)
            return
        attach = self._attach or _allure_attach
        attach(png, title)


def _allure_attach(png: bytes, title: str) -> None:
    import allure

    allure.attach(png, name=title, attachment_type=allure.attachment_type.PNG)


_REGISTERED: StepScreenshotListener | None = None


def register_if_enabled(environ: dict[str, str] | None = None) -> StepScreenshotListener | None:
    """Register the listener with Allure once, when the flag is on."""
    global _REGISTERED
    if not enabled(environ) or _REGISTERED is not None:
        return _REGISTERED
    _REGISTERED = StepScreenshotListener()
    allure_commons.plugin_manager.register(_REGISTERED)
    return _REGISTERED
