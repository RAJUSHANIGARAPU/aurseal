"""Waiting for a new backend response after a mark, refusals included."""

from __future__ import annotations

from types import SimpleNamespace

from ui.components.response_capture import ResponseCapture

PRICE = "https://demo.example.test/api/price"


def _resp(status=200, body=b"{}", url=PRICE):
    return SimpleNamespace(url=url, status=status, body=lambda: body)


class FakePage:
    """Delivers queued responses to the listener only while the test waits on the page."""

    def __init__(self, deliver_after_ms=None, response=None):
        self.listeners = []
        self.waited_ms = 0
        self.deliver_after_ms = deliver_after_ms
        self.response = response

    def on(self, event, handler):
        self.listeners.append(handler)

    def remove_listener(self, event, handler):
        self.listeners.remove(handler)

    def wait_for_timeout(self, ms):
        self.waited_ms += ms
        if self.deliver_after_ms is not None and self.waited_ms >= self.deliver_after_ms:
            self.deliver_after_ms = None
            for handler in list(self.listeners):
                handler(self.response)


def test_waits_until_a_response_arrives_after_the_mark():
    page = FakePage(deliver_after_ms=300, response=_resp(body=b'{"total": 227.5}'))
    with ResponseCapture(page, "/price") as prices:
        prices.on_response(_resp(body=b'{"total": 32.5}'))  # the stale, seeded figure
        mark = prices.mark()
        assert prices.await_after(mark, timeout_ms=2000, poll_ms=100)
    assert page.waited_ms == 300
    assert [s.body_head for s in prices.since(mark)] == ['{"total": 227.5}']
    assert str(prices.amount_at("total")) == "227.50"


def test_times_out_when_nothing_new_arrives():
    page = FakePage()
    with ResponseCapture(page, "/price") as prices:
        prices.on_response(_resp())
        assert not prices.await_after(prices.mark(), timeout_ms=500, poll_ms=100)
    assert page.waited_ms == 500


def test_refusals_are_recorded_with_their_body_but_latest_stays_2xx():
    page = FakePage(deliver_after_ms=100, response=_resp(422, b'{"error": "seats above cap"}'))
    with ResponseCapture(page, "/price") as prices:
        prices.on_response(_resp(body=b'{"total": 32.5}'))
        mark = prices.mark()
        assert prices.await_after(mark, timeout_ms=1000)
    (seen,) = prices.since(mark)
    assert (seen.status, seen.ok) == (422, False) and "seats above cap" in seen.body_head
    assert prices.latest == {"total": 32.5} and prices.count == 1


def test_only_matching_urls_are_recorded_and_bodies_are_cut():
    page = FakePage()
    with ResponseCapture(page, "/price") as prices:
        prices.on_response(_resp(url="https://demo.example.test/api/other"))
        prices.on_response(_resp(body=b"x" * 1000))
    assert len(prices.responses) == 1 and len(prices.responses[0].body_head) == 300
