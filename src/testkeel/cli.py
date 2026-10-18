"""Command-line entry point (``testkeel``)."""

from __future__ import annotations

import typer

from testkeel import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)


@app.command("version")
def show_version() -> None:
    """Print the installed version."""
    typer.echo(__version__)
