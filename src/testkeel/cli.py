"""Command-line entry point (``testkeel``).

Exit codes for ``report``: 0 written, 2 usage error, 3 input error (an unreadable or
unrecognised report, or zero tests found: an empty report is never a green one).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from testkeel import __version__
from testkeel.importers import ReportError, registry
from testkeel.model.v1 import RunUpload, TestRecord
from testkeel.reporting.html import render_html

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)


@app.command("version")
def show_version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command("report")
def report(
    files: Annotated[list[Path], typer.Argument(help="JUnit XML or Playwright JSON reports.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Directory to write.")] = Path(
        "site"
    ),
    title: Annotated[str, typer.Option(help="Page title.")] = "Test report",
    format: Annotated[
        str | None, typer.Option("--format", help="Skip format detection (junit, playwright).")
    ] = None,
) -> None:
    """Render a self-contained HTML summary of one or more result files."""
    records: list[TestRecord] = []
    for path in files:
        if not path.is_file():
            typer.echo(f"error: {path}: no such file", err=True)
            raise typer.Exit(EXIT_INPUT)
        try:
            found = registry.parse_file(path, format)
        except ReportError as exc:
            typer.echo(f"error: {path}: {exc}", err=True)
            raise typer.Exit(EXIT_INPUT) from exc
        typer.echo(f"{path}: {len(found)} results")
        records.extend(found)
    if not records:
        typer.echo("error: zero tests found; an empty report is not a passing one", err=True)
        raise typer.Exit(EXIT_INPUT)
    upload = RunUpload(records=tuple(records), started_at=datetime.now(timezone.utc))
    output.mkdir(parents=True, exist_ok=True)
    target = output / "index.html"
    target.write_text(render_html(upload, title=title), encoding="utf-8")
    typer.echo(f"wrote {target}")
