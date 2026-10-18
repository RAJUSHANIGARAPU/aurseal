"""Submit outcome capture across the three surfaces, driven against a fake page."""

from __future__ import annotations

import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from oracles.rejection import Signal
from ui.components.submit_outcome import (
    DEFAULT_ALERTS,
    DEFAULT_INLINE_ERRORS,
    SubmitResult,
    discard_draft_if_present,
    submit_and_capture,
)


class _Texts:
    def __init__(self, texts):
        self.texts = texts

    def all_inner_texts(self):
        return list(self.texts)


class _Clickable:
    def __init__(self, on_click=None, present=True):
        self.on_click = on_click
        self.present = present
        self.clicks = 0

    def filter(self, **kw):
        return self

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.present else 0

    def click(self, **kw):
        self.clicks += 1
        if self.on_click:
            self.on_click()


class _Page:
    """Inline errors and alerts as lists; one response per submit, matched by predicate."""

    def __init__(self, response=None, errors_after=(), alerts_after=(), errors_before=()):
        self.errors = list(errors_before)
        self.alerts: list[str] = []
        self.response = response
        self.errors_after = list(errors_after)
        self.alerts_after = list(alerts_after)
        self.submitted = False
        self.keyboard = SimpleNamespace(pressed=[], press=lambda k: self.keyboard.pressed.append(k))
        self.buttons: dict = {}
        self.menu_items: dict = {}
        self.url = "https://app.example.test/form"

    def _submit(self):
        self.submitted = True
        self.errors = self.errors + self.errors_after
        self.alerts = self.alerts + self.alerts_after

    def locator(self, selector):
        if selector == DEFAULT_INLINE_ERRORS:
            return _Texts(self.errors)
        if selector == DEFAULT_ALERTS:
            return _Texts(self.alerts)
        raise AssertionError(selector)

    def get_by_role(self, role, name=None, exact=None):
        if role == "button":
            return self.buttons.get(name) or _Clickable(self._submit)
        if role == "menuitem":
            return self.menu_items.get(name, _Clickable(present=False))
        raise AssertionError(role)

    @contextmanager
    def expect_response(self, predicate, timeout=None):
        holder = SimpleNamespace(value=None)
        yield holder
        if self.response is not None and predicate(self.response):
            holder.value = self.response
            return
        raise PlaywrightTimeoutError(f"Timeout {timeout}ms exceeded")

    def wait_for_load_state(self, state, timeout=None):
        pass


def _response(status, body="", method="POST", url="https://app.example.test/api/person/submit"):
    return SimpleNamespace(
        status=status, url=url, text=lambda: body, request=SimpleNamespace(method=method)
    )


def test_a_4xx_body_is_a_rejection_even_with_no_inline_message():
    page = _Page(_response(422, '{"errors":[{"field":"birthDate","message":"too young"}]}'))
    outcome = submit_and_capture(page, "Next", "/submit", advanced=lambda p: False)
    assert outcome.status == 422
    assert outcome.mentions("too young")
    assert outcome.body_json()["errors"][0]["field"] == "birthDate"
    assert outcome.rejected
    assert set(outcome.signals().fired) == {Signal.API_STATUS, Signal.STATE_UNCHANGED}


def test_an_inline_message_counts_while_the_api_said_200():
    page = _Page(_response(200, "{}"), errors_after=["Postcode is invalid"])
    outcome = submit_and_capture(page, "Next", re.compile(r"/submit$"), advanced=lambda p: False)
    assert outcome.status == 200
    assert outcome.inline_errors == ["Postcode is invalid"]
    assert outcome.mentions(re.compile("postcode", re.I))
    assert Signal.FIELD_MESSAGE in outcome.signals().fired


def test_messages_already_on_screen_are_not_new_signals():
    page = _Page(
        _response(200, "{}"),
        errors_before=["Name is required"],
        errors_after=["Name is required"],
    )
    outcome = submit_and_capture(page, "Next", "/submit", advanced=lambda p: True)
    assert outcome.inline_errors == ["Name is required"]
    page2 = _Page(_response(200, "{}"), errors_before=["Name is required"])
    accepted = submit_and_capture(page2, "Next", "/submit", advanced=lambda p: True)
    assert accepted.inline_errors == []
    assert not accepted.rejected


def test_no_matching_call_is_status_none_not_an_error():
    page = _Page(_response(200, url="https://app.example.test/api/other"))
    outcome = submit_and_capture(page, "Next", "/submit", timeout_ms=10, advanced=lambda p: False)
    assert outcome.status is None and not outcome.responded
    assert outcome.rejected  # state unchanged


def test_method_narrows_the_match():
    page = _Page(_response(200, method="GET"))
    assert submit_and_capture(page, "Next", "/submit", method="POST").status is None
    page = _Page(_response(204, method="GET"))
    assert submit_and_capture(page, "Next", "/submit", method=None).status == 204


def test_callable_submit_and_page_alerts():
    page = _Page(_response(500, "oops"), alerts_after=["Something went wrong"])
    outcome = submit_and_capture(page, page._submit, lambda url: url.endswith("/submit"))
    assert outcome.alerts == ["Something went wrong"]
    assert outcome.advanced is None
    assert "status=500" in outcome.describe()


def test_signals_accept_range_probe_extras():
    outcome = SubmitResult(status=200, advanced=True)
    signals = outcome.signals(entered="150", read_back="100")
    assert signals.entered == "150" and not outcome.rejected


class TestDiscardDraft:
    def test_discards_through_the_menu(self):
        page = _Page()
        page.buttons["Manage"] = _Clickable()
        item = _Clickable()
        page.menu_items["Discard draft"] = item
        assert discard_draft_if_present(page, "Manage", "Discard draft")
        assert item.clicks == 1

    def test_no_item_is_reported_not_raised(self):
        page = _Page()
        page.buttons["Manage"] = _Clickable()
        assert not discard_draft_if_present(page, "Manage", "Discard draft")
        assert page.keyboard.pressed == ["Escape"]

    def test_no_menu(self):
        page = _Page()
        page.buttons["Manage"] = _Clickable(present=False)
        assert not discard_draft_if_present(page, "Manage", "Discard draft")

    def test_errors_never_escape(self):
        page = _Page()

        def boom(**kw):
            raise RuntimeError("detached")

        broken = _Clickable()
        broken.click = boom
        page.buttons["Manage"] = broken
        assert not discard_draft_if_present(page, "Manage", "Discard draft")


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_every_4xx_fires_the_api_signal(status):
    assert Signal.API_STATUS in SubmitResult(status=status).signals().fired


class _NavigatedPage(_Page):
    """The submit call's body is gone once the page navigates: reading it late raises."""

    def __init__(self):
        def unreadable():
            raise RuntimeError("No resource with given identifier found")

        super().__init__(
            response=SimpleNamespace(
                status=202,
                url="https://app.example.test/api/jobs/start",
                text=unreadable,
                request=SimpleNamespace(method="POST"),
            )
        )


def test_an_unreadable_body_is_logged_not_silently_empty(monkeypatch):
    from ui.components import submit_outcome as module

    warnings: list[str] = []
    monkeypatch.setattr(module.logger, "warning", lambda msg, *a: warnings.append(msg % a))
    outcome = submit_and_capture(_NavigatedPage(), "Generate", "/api/jobs/start", settle_ms=0)
    assert outcome.status == 202 and outcome.body == ""
    assert any("could not be read" in w for w in warnings), warnings
