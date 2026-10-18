"""generate-test-from-trace: trace selection, refusals, file placement, conventions."""

from __future__ import annotations

import json
import os
import time
import zipfile

import pytest

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import traces

TEST_CODE = "import pytest\n\n\ndef test_checkout(page):\n    pass\n"
PAGE_CODE = (
    "from core.base_page import BasePage\n\n\nclass CheckoutPage(BasePage):\n"
    "    def open(self):\n        return self\n"
)


class FakeGenerator:
    def __init__(self, test=TEST_CODE, page=PAGE_CODE):
        self.test, self.page = test, page
        self.calls = []

    def generate_from_trace(self, trace_path, page_name):
        self.calls.append(("trace", trace_path, page_name))
        return self.test

    def generate_page_object(self, page_name, page_url, page_description):
        self.calls.append(("page", page_name, page_url, page_description))
        return self.page


def make_trace(path, url="https://shop.example.test/orders/42/checkout"):
    with zipfile.ZipFile(path, "w") as archive:
        events = [
            {"type": "context-options"},
            {"type": "before", "method": "goto", "params": {"url": url}},
        ]
        archive.writestr("trace.trace", "\n".join(json.dumps(e) for e in events))
    return path


@pytest.fixture
def dirs(tmp_path):
    d = {
        "trace_dir": tmp_path / "traces",
        "tests_dir": tmp_path / "tests",
        "pages_dir": tmp_path / "pages",
    }
    for p in d.values():
        p.mkdir()
    return d


def test_without_arguments_the_newest_trace_is_used_and_named(dirs):
    old = make_trace(dirs["trace_dir"] / "old.zip")
    os.utime(old, (time.time() - 100, time.time() - 100))
    new = make_trace(dirs["trace_dir"] / "new.zip")
    gen = FakeGenerator()
    out = traces.generate(None, None, generator=gen, **dirs)
    assert out.code == 0
    assert f"trace: {new}" in out.messages
    assert gen.calls[0] == ("trace", new, "checkout")


@pytest.mark.parametrize("kind", ["missing", "not-zip", "zip-without-trace"])
def test_a_bad_trace_stops_before_writing(dirs, tmp_path, kind):
    path = tmp_path / ("x.zip" if kind != "not-zip" else "x.txt")
    if kind == "not-zip":
        path.write_text("hello", encoding="utf-8")
    elif kind == "zip-without-trace":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "x")
    out = traces.generate(path, "x", generator=FakeGenerator(), **dirs)
    assert out.code == 1 and out.written == []
    assert list(dirs["tests_dir"].iterdir()) == [] and list(dirs["pages_dir"].iterdir()) == []


def test_the_test_lands_in_the_ui_tests_folder(dirs, tmp_path):
    trace = make_trace(tmp_path / "t.zip")
    (dirs["pages_dir"] / "checkout_page.py").write_text("x = 1\n", encoding="utf-8")
    out = traces.generate(trace, "checkout", generator=FakeGenerator(), **dirs)
    assert out.written == [dirs["tests_dir"] / "test_checkout.py"]


def test_an_existing_file_is_not_overwritten_without_confirmation(dirs, tmp_path):
    trace = make_trace(tmp_path / "t.zip")
    target = dirs["tests_dir"] / "test_checkout.py"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    (dirs["pages_dir"] / "checkout_page.py").write_text("x = 1\n", encoding="utf-8")
    out = traces.generate(trace, "checkout", generator=FakeGenerator(), **dirs)
    assert out.code == 1 and target.read_text() == "ORIGINAL\n"
    out = traces.generate(trace, "checkout", overwrite=True, generator=FakeGenerator(), **dirs)
    assert out.code == 0 and target.read_text() == TEST_CODE


def test_a_page_object_only_when_asked_or_missing(dirs, tmp_path):
    trace = make_trace(tmp_path / "t.zip")
    gen = FakeGenerator()
    out = traces.generate(trace, "checkout", generator=gen, **dirs)  # none exists yet
    assert dirs["pages_dir"] / "checkout_page.py" in out.written
    page_call = next(c for c in gen.calls if c[0] == "page")
    assert page_call[1] == "CheckoutPage" and page_call[2] == "/orders/{id}/checkout"

    gen2 = FakeGenerator()
    traces.generate(trace, "checkout", overwrite=True, generator=gen2, **dirs)
    assert not any(c[0] == "page" for c in gen2.calls)  # exists and not asked
    gen3 = FakeGenerator()
    traces.generate(trace, "checkout", overwrite=True, page_object=True, generator=gen3, **dirs)
    assert any(c[0] == "page" for c in gen3.calls)


def test_a_compile_error_stops_before_tests_run(dirs, tmp_path):
    trace = make_trace(tmp_path / "t.zip")
    out = traces.generate(trace, "checkout", generator=FakeGenerator(test="def broken(:\n"), **dirs)
    assert out.code == 2 and out.written == []


def test_sleeps_and_a_page_object_not_on_the_base_page_are_refused(dirs, tmp_path):
    trace = make_trace(tmp_path / "t.zip")
    sleepy = TEST_CODE + "    page.wait_for_timeout(500)\n"
    assert (
        traces.generate(trace, "checkout", generator=FakeGenerator(test=sleepy), **dirs).code == 1
    )
    loose = PAGE_CODE.replace("(BasePage)", "")
    assert traces.generate(trace, "checkout", generator=FakeGenerator(page=loose), **dirs).code == 1
    assert list(dirs["tests_dir"].iterdir()) == []


def test_without_credentials_nothing_is_written(dirs, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = traces.generate(make_trace(tmp_path / "t.zip"), "checkout", **dirs)
    assert out.code == 1 and "unavailable" in " ".join(out.messages)
    assert list(dirs["tests_dir"].iterdir()) == []


def test_the_command_is_manual_only_and_asks_before_overwriting():
    meta, body = read_command("generate-test-from-trace")
    flat = " ".join(body.split())
    assert meta["disable-model-invocation"] is True
    assert claude_md_lists("generate-test-from-trace")
    for promise in (
        "ask the user",
        "writes nothing",
        "No fixed sleeps",
        "extend `BasePage`",
        "three newest traces",
    ):
        assert promise in flat, promise
