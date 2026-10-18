"""AbsenceWatch in real Chromium against local static pages (loopback only)."""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ui.components.absence_watch import AbsenceWatch

pytestmark = pytest.mark.demo_ui

FLASH = """<!doctype html><html><body><h1>Checkout</h1><div id="slot"></div>
<script>
const slot = document.getElementById("slot");
setTimeout(() => { slot.innerHTML = "<button>Skip review</button>"; }, 300);
setTimeout(() => { slot.innerHTML = ""; }, 1000);
</script></body></html>"""

PLAIN = """<!doctype html><html><body><h1>Checkout</h1><p id="p">Ready</p></body></html>"""


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def site(tmp_path):
    (tmp_path / "flash.html").write_text(FLASH, encoding="utf-8")
    (tmp_path / "plain.html").write_text(PLAIN, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(tmp_path)))
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_phrase_shown_briefly_is_still_reported(page, site):
    page.goto(f"{site}/plain.html")
    watch = AbsenceWatch(page, ["Skip review"]).install()
    page.goto(f"{site}/flash.html")
    page.wait_for_timeout(2000)  # the flash is over long before this
    assert page.get_by_text("Skip review").count() == 0
    assert watch.was_seen("Skip review")
    watch.assert_seen("Skip review", "the page flashes it")


def test_a_sighting_survives_a_reload(page, site):
    page.goto(f"{site}/plain.html")
    watch = AbsenceWatch(page, ["Temporary offer"]).install()
    page.evaluate("document.getElementById('p').textContent = 'Temporary offer'")
    page.wait_for_timeout(500)
    page.reload()
    assert page.get_by_text("Temporary offer").count() == 0
    assert watch.seen() == ["Temporary offer"]
    watch.assert_live("after a reload the next document is armed too")


def test_absence_holds_while_the_control_was_seen(page, site):
    page.goto(f"{site}/flash.html")
    watch = AbsenceWatch(page, ["Checkout", "Delete account"]).install()
    page.wait_for_timeout(1200)
    watch.assert_seen("Checkout", "every page of the flow has it")
    watch.assert_never_seen("Delete account", "this flow never offers deletion")


def test_installing_on_a_loaded_page_catches_later_text(page, site):
    page.goto(f"{site}/plain.html")
    watch = AbsenceWatch(page, ["Added later"]).install()
    page.evaluate(
        "() => { const b = document.createElement('b'); b.textContent = 'Added later';"
        " document.body.appendChild(b); }"
    )
    page.wait_for_timeout(600)
    assert watch.was_seen("Added later")


def test_a_second_watch_starts_empty(page, site):
    page.goto(f"{site}/plain.html")
    first = AbsenceWatch(page, ["Ready"]).install()
    page.wait_for_timeout(400)
    assert first.was_seen("Ready")
    page.evaluate("document.getElementById('p').textContent = 'Changed'")
    second = AbsenceWatch(page, ["Ready"]).install()
    page.wait_for_timeout(400)
    assert second.seen() == []
    second.assert_live("the second watch is armed")


def test_arming_twice_in_one_document_records_once(page, site):
    page.goto(f"{site}/plain.html")
    watch = AbsenceWatch(page, ["Ready"]).install()
    page.evaluate(watch.script)
    page.evaluate(watch.script)
    page.evaluate("document.getElementById('p').append(' again')")
    page.wait_for_timeout(600)
    assert watch.seen() == ["Ready"]
    assert page.evaluate(f"sessionStorage.getItem({watch.key!r})") == '["Ready"]'
