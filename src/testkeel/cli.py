"""testkeel: scaffold take-home test assignments, check them for leaks, report on results.

Exit codes. new-assignment: 0 created, 1 unexpected error, 2 usage error (also a planned
type), 3 output dir not empty, 4 leak gate failed, 5 lock or self-check failed, 6 missing
prerequisite, 130 interrupted. assignment check: 0 clean, 2 cannot run, 4 findings.
report: 0 written, 2 usage, 3 unreadable input or zero tests found.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from testkeel import __version__
from testkeel.assignment import scaffold
from testkeel.assignment.types import TYPES
from testkeel.importers import ReportError, registry
from testkeel.model.v1 import RunUpload, TestRecord
from testkeel.reporting.html import render_html

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
assignment_app = typer.Typer(no_args_is_help=True, help="Work with an existing project.")
app.add_typer(assignment_app, name="assignment")


@app.command("version")
def show_version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command("list-types")
def list_types(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Assignment types: available now, or planned."""
    if as_json:
        typer.echo(json.dumps([t.as_dict() for t in TYPES], indent=2))
        return
    for kind in TYPES:
        needs = f" (needs {', '.join(kind.needs)})" if kind.needs else ""
        typer.echo(f"{kind.name:<12} {kind.status:<9} {kind.description}{needs}")


def _interactive(non_interactive: bool) -> bool:
    return not non_interactive and sys.stdin.isatty()


@app.command("new-assignment")
def new_assignment(
    type_: Annotated[str | None, typer.Option("--type", help="See list-types.")] = None,
    name: Annotated[str | None, typer.Option(help="Project name (free text, slugged).")] = None,
    company: Annotated[str, typer.Option(help="Only rendered into the README.")] = "",
    task_file: Annotated[Path | None, typer.Option(help="Task brief, copied to .task/.")] = None,
    target_url: Annotated[str | None, typer.Option(help="Web app under test.")] = None,
    api_base_url: Annotated[str | None, typer.Option(help="API under test.")] = None,
    python: Annotated[str, typer.Option(help="3.11, 3.12 or 3.13.")] = "3.12",
    report: Annotated[str, typer.Option(help="html, allure or both.")] = "html",
    ci: Annotated[str, typer.Option(help="github or none.")] = "github",
    docker: Annotated[bool, typer.Option("--docker/--no-docker")] = True,
    git: Annotated[bool, typer.Option("--git/--no-git", help="git init, no commit.")] = True,
    credit: Annotated[bool, typer.Option(help="One README footer line naming testkeel.")] = False,
    lock: Annotated[bool, typer.Option("--lock/--no-lock", help="Run uv lock (PyPI only).")] = True,
    updatable: Annotated[
        bool, typer.Option(help="Keep .copier-answers.yml, naming the public repo and tag.")
    ] = False,
    output_dir: Annotated[Path | None, typer.Option(help="Default: ./<slug>.")] = None,
    dry_run: Annotated[bool, typer.Option(help="List what would be written.")] = False,
    force: Annotated[bool, typer.Option(help="Write into a non-empty dir.")] = False,
    non_interactive: Annotated[bool, typer.Option(help="Never prompt.")] = False,
) -> None:
    """Render a new assignment project, leak-gate it, then write it."""
    if _interactive(non_interactive):
        type_ = type_ or typer.prompt("Type (see list-types)")
        name = name or typer.prompt("Project name")
    missing = [flag for flag, value in (("--type", type_), ("--name", name)) if not value]
    if missing:
        typer.echo(f"error: missing {', '.join(missing)}", err=True)
        raise typer.Exit(scaffold.USAGE)
    assert type_ is not None and name is not None
    result = scaffold.new_assignment(
        scaffold.Options(
            type=type_,
            name=name,
            company=company,
            task_file=task_file,
            target_url=target_url,
            api_base_url=api_base_url,
            python_version=python,
            report=report,
            docker=docker,
            ci=ci,
            git=git,
            credit=credit,
            lock=lock,
            updatable=updatable,
            output_dir=output_dir,
            dry_run=dry_run,
            force=force,
        )
    )
    for line in result.lines:
        typer.echo(line, err=result.code != scaffold.OK)
    raise typer.Exit(result.code)


@assignment_app.command("check")
def assignment_check(
    path: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable findings.")] = False,
    strict: Annotated[bool, typer.Option(help="A missing denylist fails.")] = False,
    allow_host: Annotated[
        list[str] | None,
        typer.Option("--allow-host", help="A host the project may name (repeatable)."),
    ] = None,
) -> None:
    """Run the leak gate over an existing project (0 clean, 4 findings, 2 cannot run)."""
    result = scaffold.check(path, strict=strict, allow_hosts=allow_host or ())
    if as_json:
        findings = [
            {"location": f.location, "line": f.line, "rule": f.rule, "entry": f.entry}
            for f in result.findings
        ]
        typer.echo(json.dumps({"exit": result.code, "findings": findings}, indent=2))
    else:
        for line in result.lines:
            typer.echo(line)
    raise typer.Exit(result.code)


@app.command("report")
def report_command(
    files: Annotated[
        list[Path], typer.Argument(help="Report files (JUnit XML, Playwright JSON, ...).")
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="Directory to write.")] = Path(
        "site"
    ),
    title: Annotated[str, typer.Option(help="Page title.")] = "Test report",
    format: Annotated[
        str | None, typer.Option("--format", help="Skip format detection (an importer name).")
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
