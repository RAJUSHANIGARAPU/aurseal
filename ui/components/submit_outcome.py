"""
Submitting a form step and recording every surface its answer can arrive on.

A validation rule can answer in three places: the submit call's 4xx body, an
inline message at the field, or simply by not letting the form move on. A test
that watches one of them reports "accepted" whenever the app happened to answer
on another — and the surface an app uses changes between releases more often
than the rule does. ``submit_and_capture`` therefore records all of them at once:

* the HTTP **status and body** of the submit call (matched by URL), or ``None``
  when no such call was made within the timeout;
* the **inline error texts** that appeared because of this submit (texts already
  on screen before it are not counted);
* page-level **alerts** that appeared;
* whether the form **advanced** (a caller-supplied check, e.g. "the next step's
  heading is visible").

A refusal is asserted **at the moment it happens**, and only means something when it is
about the rule under test. Two preconditions, checked right before the click, make that
reusable:

* ``required_filled(page)`` returns the names of required inputs that are still empty.
  Any name means the form would refuse for *that* reason, so the submit is not sent and
  the outcome is "not exercised";
* ``before_submit(page)`` returns a reason when the probe value is no longer in its
  field (the app reformatted, clamped or cleared it). The bad value was never submitted,
  so again: not exercised.

``SubmitResult.exercised`` carries this to
:func:`verification.outcome_classifier.submit_rule_outcome`, which turns an unmet
precondition into an abstention, never into "accepted".

Deciding whether that adds up to a rejection is not done here: ``signals()``
hands the observations to ``oracles.rejection.RejectionSignals``, which already
knows the traps (a blank read-back is not a rejection, a pre-existing message is
not a signal).

``discard_draft_if_present`` is the matching clean-up for an abandoned form: open
a menu and choose a discard item when both are there, and report — never raise —
when they are not. It is best effort by design; a data-layer clean-up (an API
delete) is the reliable one when the system offers it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.logger import logger
from oracles.rejection import RejectionSignals, appeared_since
from ui.components.wizard import settle, visible_button

DEFAULT_INLINE_ERRORS = "[data-field-error], .field-error, [role='alert'][data-field]"
DEFAULT_ALERTS = "[role='alert']:not([data-field])"
UrlMatch = str | re.Pattern[str] | Callable[[str], bool]


@dataclass(frozen=True)
class SubmitResult:
    status: int | None
    body: str = ""
    inline_errors: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    advanced: bool | None = None
    exercised: bool = True
    not_exercised_reason: str | None = None

    @classmethod
    def not_exercised(cls, reason: str) -> SubmitResult:
        """A precondition failed, so the submit was not sent."""
        return cls(status=None, exercised=False, not_exercised_reason=reason)

    @property
    def responded(self) -> bool:
        return self.status is not None

    def body_json(self) -> object | None:
        try:
            return json.loads(self.body) if self.body else None
        except ValueError:
            return None

    def mentions(self, text: str | re.Pattern[str]) -> bool:
        """True when ``text`` appears on any surface: the body, an inline error, an alert."""
        surfaces = [self.body, *self.inline_errors, *self.alerts]
        if isinstance(text, re.Pattern):
            return any(text.search(s) for s in surfaces)
        needle = text.lower()
        return any(needle in s.lower() for s in surfaces)

    def signals(self, **extra) -> RejectionSignals:
        """The observations as rejection signals; ``extra`` adds entered/allowed/read_back."""
        return RejectionSignals(
            api_status=self.status,
            inline_errors=list(self.inline_errors),
            page_alerts=list(self.alerts),
            state_changed=self.advanced,
            **extra,
        )

    @property
    def rejected(self) -> bool:
        """A positive rejection signal on an exercised submit (never on one not sent)."""
        return self.exercised and self.signals().rejected

    def describe(self) -> str:
        if not self.exercised:
            return f"NOT EXERCISED: {self.not_exercised_reason}"
        body = self.body if len(self.body) <= 300 else self.body[:300] + "..."
        return (
            f"status={self.status} advanced={self.advanced} inline={self.inline_errors} "
            f"alerts={self.alerts} body={body!r}"
        )


def _url_predicate(match: UrlMatch) -> Callable[[str], bool]:
    if isinstance(match, str):
        return lambda url: match in url
    if isinstance(match, re.Pattern):
        return lambda url: match.search(url) is not None
    return match


def _texts(locator) -> list[str]:
    try:
        return [t.strip() for t in locator.all_inner_texts() if t and t.strip()]
    except Exception:  # noqa: BLE001 - a detached locator reads as "nothing shown"
        return []


def submit_and_capture(
    page,
    submit: str | re.Pattern[str] | Callable[[], None],
    response_match: UrlMatch,
    *,
    advanced: Callable[[object], bool] | None = None,
    method: str | None = "POST",
    inline_errors: str = DEFAULT_INLINE_ERRORS,
    alerts: str = DEFAULT_ALERTS,
    timeout_ms: int = 15000,
    settle_ms: int = 8000,
    before_submit: Callable[[object], str | None] | None = None,
    required_filled: Callable[[object], list[str]] | None = None,
) -> SubmitResult:
    """
    Run ``submit`` (a visible button's name, or a callable) and record the outcome.

    ``response_match`` selects the submit call by URL (substring, regex or
    callable); ``method`` narrows it further (``None`` = any method). When no
    matching response arrives within ``timeout_ms`` the outcome has
    ``status=None`` — the request may never have been sent, which is itself a
    finding (client-side validation blocked it).

    ``required_filled`` and ``before_submit`` run right before the click; when either
    reports a problem nothing is clicked and the result is ``exercised=False``.
    """
    reason = _unmet_precondition(page, before_submit, required_filled)
    if reason is not None:
        logger.warning("submit not exercised: %s", reason)
        return SubmitResult.not_exercised(reason)
    matches = _url_predicate(response_match)

    def wanted(response) -> bool:
        if not matches(response.url):
            return False
        return method is None or response.request.method.upper() == method.upper()

    errors_before = _texts(page.locator(inline_errors))
    alerts_before = _texts(page.locator(alerts))

    def click() -> None:
        if callable(submit) and not isinstance(submit, re.Pattern):
            submit()
        else:
            visible_button(page, submit).click()

    status: int | None = None
    body = ""
    try:
        with page.expect_response(wanted, timeout=timeout_ms) as caught:
            click()
        response = caught.value
        status = response.status
        try:
            body = response.text()
        except Exception:  # noqa: BLE001 - a redirect or aborted body is unreadable
            logger.warning("the submit call's response body could not be read: %s", response.url)
            body = ""
    except (PlaywrightTimeoutError, TimeoutError):  # "no call" is an outcome, not an error
        logger.info("no response matching the submit call within %dms", timeout_ms)

    settle(page, settle_ms)
    outcome = SubmitResult(
        status=status,
        body=body,
        inline_errors=appeared_since(errors_before, _texts(page.locator(inline_errors))),
        alerts=appeared_since(alerts_before, _texts(page.locator(alerts))),
        advanced=None if advanced is None else bool(advanced(page)),
    )
    logger.info("submit outcome: %s", outcome.describe())
    return outcome


def _unmet_precondition(
    page,
    before_submit: Callable[[object], str | None] | None,
    required_filled: Callable[[object], list[str]] | None,
) -> str | None:
    if required_filled is not None:
        empty = [name for name in required_filled(page) if name]
        if empty:
            return (
                f"required input(s) still empty: {', '.join(empty)}; the refusal would not "
                "be about the rule under test"
            )
    if before_submit is not None:
        gone = before_submit(page)
        if gone:
            return f"the probe value is no longer in its field: {gone}"
    return None


def discard_draft_if_present(
    page,
    menu_button: str | re.Pattern[str],
    discard_item: str | re.Pattern[str],
    *,
    confirm_button: str | re.Pattern[str] | None = None,
    timeout_ms: int = 3000,
) -> bool:
    """
    Best-effort removal of an abandoned draft through the UI. True when it was
    discarded, False (logged) when the menu or item was not there.
    """
    try:
        trigger = visible_button(page, menu_button)
        if trigger.count() == 0:
            logger.info("draft discard: no '%s' menu on this page", menu_button)
            return False
        trigger.click(timeout=timeout_ms)
        item = page.get_by_role("menuitem", name=discard_item).filter(visible=True).first
        if item.count() == 0:
            logger.info("draft discard: no '%s' item; nothing to discard", discard_item)
            page.keyboard.press("Escape")
            return False
        item.click(timeout=timeout_ms)
        if confirm_button is not None:
            visible_button(page, confirm_button).click(timeout=timeout_ms)
        settle(page, timeout_ms)
        return True
    except Exception as exc:  # noqa: BLE001 - clean-up never fails a test
        logger.warning("draft discard failed: %s", exc)
        return False
