"""The dashboard renders every page from a fixture DB, self-contained and parseable."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from results.cli import main
from results.dashboard import render
from results.models import EvidenceRecord
from tests.results._history import add_run

_ATTR = re.compile(r'\b(?:href|src)="([^"]*)"')
VOID = {"meta", "br", "input", "img", "link", "hr", "circle", "rect", "line", "polyline"}


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> with open {self.stack[-3:]}")
            return
        self.stack.pop()


def _seed(store):
    add_run(store, "r1", 1, {"a": "passed", "b": "passed"}, revisions={"a": "rev1"})
    add_run(
        store,
        "r2",
        2,
        {"a": "failed", "b": ["failed", "passed"]},
        revisions={"a": "rev2"},
        fingerprints={"a": "abc123"},
        messages={"a": "AssertionError: <total> was 0"},
    )
    store.add_evidence(
        [
            EvidenceRecord("r2", "screenshot", "reports/shots/a.png", nodeid="tests/test_x.py::a"),
            EvidenceRecord(
                "r2", "trace", "https://outside.example.test/t.zip", nodeid="tests/test_x.py::a"
            ),
        ]
    )


def test_every_page_exists_parses_and_stays_inside(store, tmp_path):
    _seed(store)
    out = tmp_path / "dash"
    written = render(store, out)
    names = {p.relative_to(out).as_posix() for p in written}
    assert {
        "index.html",
        "runs/r1.html",
        "runs/r2.html",
        "cases/c-a.html",
        "cases/c-b.html",
        "unstable.html",
        "slowest.html",
        "catalogue.html",
        "search.html",
    } <= names
    for path in written:
        html = path.read_text()
        parser = _Balance()
        parser.feed(html)
        assert not parser.errors and parser.stack == [], (path, parser.errors, parser.stack)
        markup = re.sub(r"<script\b.*?</script>", "", html, flags=re.S)
        for target in _ATTR.findall(markup):
            assert not re.match(r"^[a-z]+:", target) and not target.startswith("//"), (path, target)
            if target and not target.startswith("#"):
                resolved = (path.parent / target).resolve()
                if "reports/shots" not in target:
                    assert resolved.exists(), (path, target)
    run2 = (out / "runs" / "r2.html").read_text()
    assert "&lt;total&gt; was 0" in run2 and "abc123" in run2
    assert "outside.example.test/t.zip" in run2  # shown as text, not a link
    assert 'href="https://outside' not in run2
    assert "Still failing" in run2 or "new failures" in run2
    case_a = (out / "cases" / "c-a.html").read_text()
    assert 'class="boundary"' in case_a  # revision changed between r1 and r2


def test_base_artifact_url_prefixes_evidence(store, tmp_path):
    _seed(store)
    out = tmp_path / "dash"
    render(store, out, base_artifact_url="https://ci.example.test/artifacts/")
    run2 = (out / "runs" / "r2.html").read_text()
    assert 'href="https://ci.example.test/artifacts/reports/shots/a.png"' in run2


def test_an_empty_store_renders_unmeasured(store, tmp_path):
    out = tmp_path / "dash"
    render(store, out)
    assert "nothing has been measured" in (out / "index.html").read_text()


def test_cli(store, tmp_path, capsys):
    _seed(store)
    assert main(["dashboard", "--out", str(tmp_path / "d")]) == 0
    assert "page(s)" in capsys.readouterr().out
