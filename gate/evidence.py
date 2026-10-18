"""Evidence collection, merged report and publishing - whatever happened in the run.

Green, red, cancelled half-way or a lane skipped: every test job stages its evidence and
uploads it as ``evidence-<job>`` (``if: always()``), and one ``publish`` job merges what
arrived into one HTML report and one step summary. A missing piece is noted, never a
reason for publishing itself to fail.

* ``python -m gate.evidence stage --job <name>`` writes the report categories file into
  every report-results directory (``reports/allure-results*``), then copies the paths in
  :data:`EVIDENCE` that exist into ``evidence/``. The upload step uploads that directory,
  so the path list lives here once instead of in every job.
* ``python -m gate.evidence publish --in <downloads> --out reports/published --expected
  offline,browser,...`` merges report results without overwriting (a same-named file from
  another lane is renamed ``<artefact>--<name>`` and the rename logged), copies JUnit XML
  the same way, renders the static HTML report with the framework's own dashboard
  renderer (``results`` store, imported from the JUnit files), and writes the summary:
  run-trust verdicts of every lane first, then totals per lane, the failing tests (capped,
  with "N more"), and one line per expected artefact that never arrived.

Publishing exits 0 unless its own tooling breaks; zero inputs is a summary that says so.
"""

from __future__ import annotations

import argparse
import filecmp
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gate.merge import JunitTotals
from reporting.allure_categories import write_categories

PREFIX = "evidence-"
STAGE_DIR = "evidence"
DEFAULT_FAILURE_CAP = 30
RESULTS_GLOB = "reports/allure-results*"
JUNIT_GLOB = "reports/junit*.xml"
TRUST_GLOB = "reports/run-trust*.txt"

# What a test job uploads, by what it is. One source of truth; the workflow reads none of it.
EVIDENCE: Mapping[str, tuple[str, ...]] = {
    "report results": (RESULTS_GLOB,),
    "screenshots": ("reports/screenshots",),
    "traces": ("reports/traces",),
    "videos": ("reports/videos",),
    "junit xml": (JUNIT_GLOB,),
    "failure bundles": ("reports/failures",),
    "logs": ("reports/logs",),
    "flakiness": ("reports/flakiness",),
    "detector status": ("reports/verdicts",),
    "run trust": (TRUST_GLOB, "reports/run-trust*.json"),
    "traceability": ("reports/traceability",),
    "coverage gaps": ("reports/traceability/gaps*",),
    "defect drafts": ("reports/traceability/defect-drafts",),
    "release verdict": ("reports/release",),
}
EVIDENCE_PATHS: tuple[str, ...] = tuple(p for paths in EVIDENCE.values() for p in paths)


def write_categories_into(directories: Iterable[str | Path]) -> list[Path]:
    """The categories file in every given directory, creating the ones that are missing."""
    return [write_categories(directory) for directory in directories]


def stage(job: str, root: str | Path = ".", out: str | Path = STAGE_DIR) -> list[str]:
    """Categories into each results dir, then copy every existing evidence path to ``out``."""
    base = Path(root)
    results_dirs = sorted(p for p in base.glob(RESULTS_GLOB) if p.is_dir())
    write_categories_into(results_dirs or [base / "reports" / "allure-results"])
    target = Path(out)
    if not target.is_absolute():
        target = base / target
    copied: list[str] = []
    for pattern in EVIDENCE_PATHS:
        for match in sorted(glob.glob(pattern, root_dir=base)):
            source = base / match
            destination = target / match
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            elif source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            else:
                continue
            copied.append(match)
    (target).mkdir(parents=True, exist_ok=True)
    (target / "JOB").write_text(job + "\n", encoding="utf-8")
    return copied


# ------------------------------------------------------------------ publishing


@dataclass
class Merge:
    copied: int = 0
    renames: list[str] = field(default_factory=list)


def _copy_without_overwrite(source: Path, target_dir: Path, artefact: str, merge: Merge) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / source.name
    if destination.exists():
        if filecmp.cmp(source, destination, shallow=False):
            return
        renamed = target_dir / f"{artefact}--{source.name}"
        merge.renames.append(f"{source.name} from {artefact} renamed to {renamed.name}")
        destination = renamed
    shutil.copy2(source, destination)
    merge.copied += 1


def artefact_dirs(inputs: str | Path) -> dict[str, Path]:
    """``{lane: directory}`` for every downloaded ``evidence-*`` artefact."""
    base = Path(inputs)
    if not base.is_dir():
        return {}
    return {
        d.name[len(PREFIX) :]: d
        for d in sorted(base.iterdir())
        if d.is_dir() and d.name.startswith(PREFIX)
    }


def merge_results(artefacts: Mapping[str, Path], out: str | Path) -> Merge:
    """Every report-results file of every artefact in one directory; nothing overwritten."""
    merge = Merge()
    target = Path(out)
    for lane, directory in artefacts.items():
        for results in sorted(directory.glob(RESULTS_GLOB)):
            for file in sorted(p for p in results.rglob("*") if p.is_file()):
                _copy_without_overwrite(file, target, f"{PREFIX}{lane}", merge)
    return merge


def merge_junit_files(artefacts: Mapping[str, Path], out: str | Path) -> Merge:
    merge = Merge()
    for lane, directory in artefacts.items():
        for file in sorted(directory.glob(JUNIT_GLOB)):
            _copy_without_overwrite(file, Path(out), f"{PREFIX}{lane}", merge)
    return merge


@dataclass(frozen=True)
class LaneTotals:
    lane: str
    totals: JunitTotals
    failed: tuple[str, ...]


def junit_totals(lane: str, files: Iterable[str | Path]) -> LaneTotals:
    tests = failures = errors = skipped = 0
    failed: list[str] = []
    for path in files:
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for case in root.iter("testcase"):
            tests += 1
            name = f"{case.get('classname') or ''}::{case.get('name') or ''}".strip(":")
            if case.find("failure") is not None:
                failures += 1
                failed.append(name)
            elif case.find("error") is not None:
                errors += 1
                failed.append(name)
            elif case.find("skipped") is not None:
                skipped += 1
    return LaneTotals(lane, JunitTotals(tests, failures, errors, skipped), tuple(failed))


def junit_summary(lanes: Sequence[LaneTotals], cap: int = DEFAULT_FAILURE_CAP) -> list[str]:
    lines = [
        "| Lane | Tests | Failures | Errors | Skipped |",
        "|---|---|---|---|---|",
    ]
    for lane in lanes:
        t = lane.totals
        lines.append(f"| {lane.lane} | {t.tests} | {t.failures} | {t.errors} | {t.skipped} |")
    failed = [f"{lane.lane}: {name}" for lane in lanes for name in lane.failed]
    if failed:
        lines += ["", "Failing tests:"]
        lines += [f"- {name}" for name in failed[:cap]]
        if len(failed) > cap:
            lines.append(f"- ... and {len(failed) - cap} more")
    return lines


def absent(expected: Iterable[str], present: Iterable[str]) -> list[str]:
    """One line per expected job with no artefact (a matrix leg uploads ``<job>--<leg>``)."""
    have = set(present)
    return [
        f"absent: {PREFIX}{lane} (the job ran but uploaded nothing, or died before uploading)"
        for lane in expected
        if lane not in have and not any(h.startswith(f"{lane}--") for h in have)
    ]


def expected_from_needs(needs: Mapping[str, Mapping]) -> tuple[list[str], list[str]]:
    """``(expected, skipped)`` from the workflow's ``toJSON(needs)``: a skipped job uploads
    nothing by design; any other result should have left evidence."""
    expected = [name for name, info in needs.items() if info.get("result") != "skipped"]
    skipped = [name for name, info in needs.items() if info.get("result") == "skipped"]
    return expected, skipped


def trust_lines(artefacts: Mapping[str, Path]) -> list[str]:
    lines = []
    for lane, directory in artefacts.items():
        for marker in sorted(directory.glob(TRUST_GLOB)):
            try:
                first = marker.read_text(encoding="utf-8").splitlines()[:1]
            except OSError:
                continue
            if first:
                lines.append(f"{lane}: {first[0]}")
    return lines


def render_report(junit_dir: Path, out: Path) -> bool:
    """Import every JUnit file into a scratch store and render the static dashboard."""
    files = sorted(junit_dir.glob("*.xml")) if junit_dir.is_dir() else []
    if not files:
        return False
    with tempfile.TemporaryDirectory(prefix="evidence-store-") as tmp:
        db = str(Path(tmp) / "results.db")
        for file in files:
            subprocess.run(
                [sys.executable, "-m", "results", "--db", db, "import", "junit", str(file)],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            [sys.executable, "-m", "results", "--db", db, "dashboard", "--out", str(out)],
            check=True,
            capture_output=True,
        )
    return True


def publish(
    inputs: str | Path,
    out: str | Path,
    expected: Sequence[str] = (),
    cap: int = DEFAULT_FAILURE_CAP,
    render: bool = True,
) -> list[str]:
    """Merge, render and return the summary lines (the caller writes them)."""
    artefacts = artefact_dirs(inputs)
    out_dir = Path(out)
    if not artefacts:
        print(f"publish: no {PREFIX}* artefact under {inputs}")
        return [
            "## Evidence",
            "",
            "No evidence was found: no test job uploaded an artefact.",
            *absent(expected, ()),
        ]
    results = merge_results(artefacts, out_dir / "allure-results")
    junit = merge_junit_files(artefacts, out_dir / "junit")
    for line in results.renames + junit.renames:
        print(f"publish: {line}")
    lanes = [junit_totals(lane, sorted(d.glob(JUNIT_GLOB))) for lane, d in artefacts.items()]
    rendered = render and render_report(out_dir / "junit", out_dir / "report")
    lines = ["## Evidence", ""]
    lines += [f"- {line}" for line in trust_lines(artefacts)] or ["- no run-trust marker found"]
    lines += ["", *junit_summary(lanes, cap), ""]
    lines += absent(expected, artefacts)
    lines.append(
        f"Report: {'reports/published/report/index.html' if rendered else 'not rendered'}"
        f" ({results.copied} result file(s) merged, {len(results.renames)} renamed)"
    )
    return lines


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="python -m gate.evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    st = sub.add_parser("stage", help="collect this job's evidence into evidence/")
    st.add_argument("--job", required=True)
    st.add_argument("--out", default=STAGE_DIR)
    pub = sub.add_parser("publish", help="merge every evidence-* artefact and summarise")
    pub.add_argument("--in", dest="inputs", default="evidence-in")
    pub.add_argument("--out", default="reports/published")
    pub.add_argument("--expected", default="", help="comma-separated job names")
    pub.add_argument("--needs-json", default=None, help="toJSON(needs): expected = not skipped")
    pub.add_argument("--cap", type=int, default=DEFAULT_FAILURE_CAP)
    pub.add_argument("--no-render", action="store_true")
    pub.add_argument("--summary", default=environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)
    if args.command == "stage":
        copied = stage(args.job, ".", args.out)
        print(f"evidence: staged {len(copied)} path(s) for {args.job}")
        return 0
    expected = [e.strip() for e in args.expected.split(",") if e.strip()]
    skipped: list[str] = []
    if args.needs_json:
        expected, skipped = expected_from_needs(json.loads(args.needs_json))
    lines = publish(args.inputs, args.out, expected, args.cap, render=not args.no_render)
    lines += [f"skipped: {name} (its condition was false on this run)" for name in skipped]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
