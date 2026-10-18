"""The static HTML report: golden file, escaping, and the ``report`` command."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testkeel import __version__
from testkeel.cli import app
from testkeel.importers import registry
from testkeel.model.identity import fingerprint
from testkeel.model.v1 import RunUpload, Status, TestRecord
from testkeel.plugin_api.v1 import REPORTERS, discover
from testkeel.reporting.html import HtmlReporter, render_html

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "report.golden.html"
WHEN = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)


def _fixture_run() -> RunUpload:
    records = [
        *registry.parse_file(FIXTURES / "junit-pytest.xml"),
        *registry.parse_file(FIXTURES / "playwright.json"),
    ]
    return RunUpload(name="nightly", records=tuple(records))


def _golden_text(html: str) -> str:
    # The version moves with every release; the golden file pins everything else.
    return html.replace(f"by testkeel {__version__}<", "by testkeel VERSION<")


def test_the_rendered_report_matches_the_golden_file():
    html = _golden_text(render_html(_fixture_run(), title="ACME Shop", generated=WHEN))
    if os.environ.get("TESTKEEL_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(html, encoding="utf-8")
    assert html == GOLDEN.read_text(encoding="utf-8")


def test_the_report_is_self_contained():
    html = render_html(_fixture_run(), generated=WHEN)
    assert "<script" not in html
    assert "http://" not in html and "https://" not in html
    assert "<link" not in html


def test_every_text_is_escaped():
    hostile = '<script>alert("x")</script>'
    record = TestRecord(
        framework="pytest",
        path=f"tests/{hostile}.py",
        suite=hostile,
        name=hostile,
        variant=hostile,
        configuration=hostile,
        fingerprint=fingerprint("pytest", "p", "s", "n"),
        status=Status.FAILED,
        message=hostile,
    )
    html = render_html(RunUpload(name=hostile, records=(record,)), title=hostile, generated=WHEN)
    assert "<script>" not in html
    assert html.count("&lt;script&gt;") >= 6


def test_long_messages_are_truncated():
    record = TestRecord(
        framework="pytest",
        name="t",
        fingerprint=fingerprint("pytest", "p", "s", "t"),
        status=Status.FAILED,
        message="x" * 5000,
    )
    html = render_html(RunUpload(records=(record,)), generated=WHEN)
    assert "x" * 500 + "…" in html and "x" * 501 not in html


def test_the_html_reporter_is_a_registered_plugin(tmp_path):
    assert isinstance(discover(REPORTERS)["html"], HtmlReporter)
    written = HtmlReporter().render(_fixture_run(), tmp_path / "out")
    assert written == tmp_path / "out" / "index.html" and written.is_file()


runner = CliRunner()


def test_the_report_command_writes_index_html(tmp_path):
    result = runner.invoke(
        app,
        [
            "report",
            str(FIXTURES / "junit-pytest.xml"),
            str(FIXTURES / "playwright.json"),
            "-o",
            str(tmp_path / "site"),
        ],
    )
    assert result.exit_code == 0, result.output
    page = (tmp_path / "site" / "index.html").read_text("utf-8")
    assert "<h1>Test report</h1>" in page


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ('<testsuite name="empty"></testsuite>', "zero tests"),
        ("name,status\n", "unrecognised"),
    ],
)
def test_the_report_command_refuses_empty_or_unknown_input(tmp_path, content, problem):
    report = tmp_path / "in.xml"
    report.write_text(content, "utf-8")
    result = runner.invoke(app, ["report", str(report), "-o", str(tmp_path / "site")])
    assert result.exit_code == 3
    assert problem in result.output
    assert not (tmp_path / "site" / "index.html").exists()


def test_a_missing_file_is_an_input_error(tmp_path):
    result = runner.invoke(app, ["report", str(tmp_path / "nope.xml")])
    assert result.exit_code == 3
