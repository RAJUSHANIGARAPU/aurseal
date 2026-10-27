"""
/detect-coverage-gaps: the CI coverage ratchets, run report-only. No parser of its own.

It runs, with the same sources as the CI steps "Coverage gaps (ratchet)" and "Workflow
action gaps (ratchet)" in ``.github/workflows/tests.yml``::

    python -m traceability gaps <GAP_SOURCES> --report-only --out reports/gaps
    python -m traceability workflow-gaps <WORKFLOW_SOURCES> --report-only --out reports/gaps

and leaves ``gaps.md`` / ``gaps.json`` and ``workflow-gaps.md`` / ``workflow-gaps.json``
in the output directory. One extra source may be named: an OpenAPI document by URL or
file, or a Postman collection file. It is passed to ``traceability gaps`` as one more
input (``--openapi`` / ``--postman``), so its items show up as new gaps against the CI
baseline, which is the honest reading: CI does not check them.

Matching is the ratchet's: explicit ``covers`` markers and requirement links for
``gaps``, string literals in test modules for ``workflow-gaps``.

    python -m tools.slash.coverage_gaps [all | <openapi url or file> | <postman file>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from traceability._util import read_json
from traceability.gaps import is_url

GAP_SOURCES = (
    "--openapi",
    "demo_sut/openapi.json",
    "--specs",
    "specs",
    "--requirements",
    "--baseline",
    "traceability/gap-baseline.json",
)
WORKFLOW_SOURCES = (
    "--config-dir",
    "demo_sut/config",
    "--baseline",
    "traceability/workflow-gap-baseline.json",
)
DEFAULT_OUT = Path("reports") / "gaps"


def extra_source(target: str) -> list[str]:
    """The ``traceability gaps`` arguments for one more source; none for ``all``."""
    if target == "all":
        return []
    if is_url(target):
        return ["--openapi", target]
    path = Path(target)
    if not path.is_file():
        raise ValueError(f"not a file or an http(s) URL: {target}")
    data = read_json(path)
    if isinstance(data, dict) and "item" in data:
        return ["--postman", target]
    return ["--openapi", target]


def commands(target: str = "all", out: Path = DEFAULT_OUT) -> list[list[str]]:
    """The two ``python -m traceability`` argument lists this command runs."""
    return [
        ["gaps", *GAP_SOURCES, *extra_source(target), "--report-only", "--out", str(out)],
        ["workflow-gaps", *WORKFLOW_SOURCES, "--report-only", "--out", str(out)],
    ]


def main(argv: list[str] | None = None) -> int:
    from traceability.cli import main as traceability

    parser = argparse.ArgumentParser(description="Specified but not tested (report only).")
    parser.add_argument("target", nargs="?", default="all")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        runs = commands(args.target, args.out)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    code = max(traceability(run) for run in runs)
    print(f"reports: {args.out / 'gaps.md'}, {args.out / 'workflow-gaps.md'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
