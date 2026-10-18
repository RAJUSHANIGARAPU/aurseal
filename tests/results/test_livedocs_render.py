"""Golden Markdown and HTML for a fixture page; build writes pages, index and status."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from results.api import dispatch
from results.cli import main
from results.livedocs import build, parse_page, render_page
from results.models import EvidenceRecord
from tests.results._livedocs_fixture import run, seed_cases, write_docs

pytestmark = pytest.mark.feature("living-documentation")

GOLDEN = Path(__file__).parent / "golden"
NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _seed(store, tmp_path):
    seed_cases(store)
    run(store, "r1", 5, {"pay": "passed", "list": "skipped"})
    store.add_evidence([EvidenceRecord("r1", "trace", "traces/pay.zip", nodeid="t.py::test_pay")])
    return parse_page(write_docs(tmp_path) / "billing.md")


def test_markdown_golden(store, tmp_path):
    page = _seed(store, tmp_path)
    assert render_page(store, page, fmt="md", now=NOW) == (GOLDEN / "livedocs.md").read_text()


def test_html_golden(store, tmp_path):
    page = _seed(store, tmp_path)
    html = render_page(store, page, fmt="html", now=NOW)
    assert html == (GOLDEN / "livedocs.html").read_text()
    assert 'href="refunds.html"' in html and "http" not in html.split("<main>")[1]


def test_build_never_writes_into_docs(store, tmp_path):
    _seed(store, tmp_path)
    docs = tmp_path / "docs"
    before = sorted(p.name for p in docs.iterdir())
    out = tmp_path / "living"
    written = build(store, docs, out, now=NOW)
    assert sorted(p.name for p in docs.iterdir()) == before
    names = {p.name for p in written}
    assert {"billing.md", "billing.html", "index.md", "index.html", "status.json"} == names
    status = json.loads((out / "status.json").read_text())
    assert status["billing"]["status"] == "unproven"  # a linked case only skipped


def test_rest_and_cli(store, tmp_path, capsys):
    _seed(store, tmp_path)
    docs = str(tmp_path / "docs")
    pages = dispatch(store, "GET", "/docs", params={"docs_dir": docs})["pages"]
    assert [p["page"] for p in pages] == ["billing"]
    one = dispatch(store, "GET", "/docs/billing", params={"docs_dir": docs})
    assert one["status"]["status"] == "unproven" and "| Case |" in one["markdown"]
    cases = dispatch(store, "GET", "/features/billing/cases")["cases"]
    assert {c["case_id"] for c in cases} == {"c-pay", "c-list"}
    assert main(["livedocs", "--docs-dir", docs, "--out", str(tmp_path / "o")]) == 0
