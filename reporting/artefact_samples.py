"""Regenerate the committed artefact samples in ``docs/artefact-samples/``.

Runs the fixed sample suite (``scripts/artefact_samples/sample_suite.py``: one pass, one
failure, one skip, one expected-red detector) in a scratch directory, exports the run's
results bundle, then copies one sample per artefact, normalised so the samples never churn
and never carry machine details:

* timestamps, epoch milliseconds, durations and compact run stamps -> fixed placeholders;
* UUIDs -> ``00000000-0000-0000-0000-00000000000N`` in order of first appearance;
* the repository path, the scratch path and the home directory -> ``$REPO``, ``$RUN``, ``~``;
* host, OS, Python and Playwright versions -> placeholders; memory addresses -> ``0x0``.

Large artefacts are trimmed to an ``.excerpt.`` file that keeps the structure. Samples are
never edited by hand: ``--check`` regenerates into a temporary directory and fails on any
difference.

``python -m reporting.artefact_samples [--out docs/artefact-samples] [--check]``
Exit 0 written / unchanged, 1 the committed samples differ, 2 the sample run broke.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "scripts" / "artefact_samples" / "sample_suite.py"
DEFAULT_OUT = ROOT / "docs" / "artefact-samples"
RUN_ID = "sample-run"
LANE = "sample"
FIXED_ISO = "2000-01-01T00:00:00Z"
_EXCERPT_CHARS = 240

_ISO = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}\\?:\d{2}(?:\\?:\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}\\?:?\d{2})?"
)
_COMPACT = re.compile(r"\b\d{8}T\d{6}Z\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_ADDRESS = re.compile(r"0x[0-9a-f]{6,}")
_EPOCH_MS = re.compile(r'"(start|stop)":(\s*)\d{10,}')
# Spaced or compact JSON alike: the results bundle is written with compact separators.
_DURATION_JSON = re.compile(r'"(\w*_ms|\w*_s|duration)":(\s*)[0-9.e-]+')
# The commit is known only inside a git checkout (null in an exported tree): always a placeholder.
_GIT_SHA = re.compile(r'"(git_sha|git_commit|commit)": (?:"[0-9a-f]{7,40}"|null)')
_THREAD = re.compile(r"\b\d+-(MainThread|Thread-\d+)")
_TIME_ATTR = re.compile(r'\b(time)="[0-9.]+"')
_HOST_ATTR = re.compile(r'\bhostname="[^"]*"')
_PROPERTY_LINES = {
    "OS": "OS=$OS",
    "Python.Version": "Python.Version=$PYTHON",
    "Playwright.Version": "Playwright.Version=$PLAYWRIGHT",
}


class Normaliser:
    """Text replacements that make a sample identical across machines and runs."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = str(run_dir.resolve())
        self.uuids: dict[str, str] = {}

    def _uuid(self, match: re.Match[str]) -> str:
        found = match.group(0)
        if found not in self.uuids:
            self.uuids[found] = f"00000000-0000-0000-0000-{len(self.uuids) + 1:012d}"
        return self.uuids[found]

    def __call__(self, text: str) -> str:
        for real, placeholder in (
            (sys.prefix, "$VENV"),
            (sys.base_prefix, "$PYTHON"),
            (self.run_dir, "$RUN"),
            (str(Path(self.run_dir).parent.resolve()), "$SCRATCH"),
            (str(ROOT), "$REPO"),
            (str(Path.home()), "~"),
        ):
            text = text.replace(real, placeholder)
        text = text.replace("/private$RUN", "$RUN").replace("/private$REPO", "$REPO")
        text = _ISO.sub(FIXED_ISO, text)
        text = _COMPACT.sub("20000101T000000Z", text)
        text = _UUID.sub(self._uuid, text)
        text = _ADDRESS.sub("0x0", text)
        text = _EPOCH_MS.sub(lambda m: f'"{m.group(1)}":{m.group(2)}0', text)
        text = _DURATION_JSON.sub(lambda m: f'"{m.group(1)}":{m.group(2)}0', text)
        text = _TIME_ATTR.sub(lambda m: f'{m.group(1)}="0.000"', text)
        text = _HOST_ATTR.sub('hostname="localhost"', text)
        text = _THREAD.sub(lambda m: f"$PID-{m.group(1)}", text)
        text = _GIT_SHA.sub(lambda m: f'"{m.group(1)}": "$SHA"', text)
        host = socket.gethostname()
        if host:
            text = re.sub(rf"\b{re.escape(host)}\b", "$HOST", text)
        lines = []
        for line in text.splitlines():
            key = line.split("=", 1)[0]
            lines.append(_PROPERTY_LINES.get(key, line) if "=" in line else line)
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _trim(value: object) -> object:
    if isinstance(value, str) and len(value) > _EXCERPT_CHARS:
        return value[:_EXCERPT_CHARS] + "...[trimmed]"
    if isinstance(value, list):
        return [_trim(v) for v in value[:5]]
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    return value


def excerpt_json(text: str) -> str:
    return json.dumps(_trim(json.loads(text)), indent=2, sort_keys=True) + "\n"


def failure_bundle(text: str) -> str:
    """The bundle's shape; the stack trace is pytest-internal and machine-specific."""
    data = json.loads(text)
    if data.get("stackTrace"):
        data["stackTrace"] = "$STACK_TRACE"
    return excerpt_json(json.dumps(data))


_BUNDLE_ORDER = {
    t: i
    for i, t in enumerate(("run", "case", "revision", "invocation", "result", "verdict", "run_end"))
}


def excerpt_ndjson(text: str, per_type: int = 2) -> str:
    """At most ``per_type`` records of each ``type``, long strings trimmed."""
    kept, seen = [], {}
    # Sorted first: the store's export order is not guaranteed across platforms.
    records = sorted(
        (json.loads(line) for line in text.splitlines() if line.strip()),
        key=lambda r: (_BUNDLE_ORDER.get(str(r.get("type")), 99), json.dumps(r, sort_keys=True)),
    )
    for record in records:
        kind = str(record.get("type", "")) if isinstance(record, dict) else ""
        seen[kind] = seen.get(kind, 0) + 1
        if seen[kind] <= per_type:
            kept.append(json.dumps(_trim(record), sort_keys=True))
    return "\n".join(kept) + "\n"


def _sorted_jsonl(text: str) -> str:
    rows = [json.dumps(json.loads(line), sort_keys=True) for line in text.splitlines() if line]
    return "\n".join(sorted(rows)) + "\n"


def run_sample_suite(run_dir: Path) -> Path:
    """Run the sample suite with ``run_dir`` as working directory; returns its reports dir."""
    reports = run_dir / "reports"
    # A minimal environment: nothing from the caller's shell or CI may leak into a sample.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
    env.update(
        {
            "TEST_RUN_ID": RUN_ID,
            "RUN_TRUST": "report",
            "RUN_TRUST_LANE": LANE,
            "RUN_TRUST_DIR": str(reports),
            "RESULTS_DB": str(reports / "results" / "results.db"),
            "VERDICTS_FILE": str(reports / "verdicts" / "verdicts.jsonl"),
            "TEST_ENV": "local",
            "BASE_URL": "http://127.0.0.1:8765/",
            "AI_TRANSPORT": "none",
            "PYTHONPATH": str(ROOT),
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pytest.ini"),
            "--rootdir",
            str(ROOT),
            str(SUITE),
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-n",
            "0",
            f"--alluredir={reports / 'allure-results'}",
            f"--junitxml={reports / 'junit-sample.xml'}",
            "-q",
        ],
        cwd=run_dir,
        env=env,
        capture_output=True,
        check=False,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "results",
            "--db",
            env["RESULTS_DB"],
            "export",
            "bundle",
            "--run-id",
            RUN_ID,
            "--out",
            str(reports / "results"),
        ],
        cwd=run_dir,
        env=env,
        capture_output=True,
        check=True,
    )
    return reports


def _one(reports: Path, pattern: str) -> Path:
    found = sorted(reports.glob(pattern))
    if not found:
        raise FileNotFoundError(f"the sample run wrote no {pattern}")
    return found[0]


def _allure_results(reports: Path) -> dict[str, str]:
    """Each result file renamed after its test, so names do not churn."""
    out = {}
    for path in sorted((reports / "allure-results").glob("*-result.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[f"allure-results/{data['name']}-result.json"] = json.dumps(
            data, indent=2, sort_keys=True
        )
    return out


# sample file name -> (source glob under reports/, transform)
SAMPLES: dict[str, tuple[str, Callable[[str], str] | None]] = {
    "allure-categories.json": ("allure-results/categories.json", None),
    "allure-environment.properties": ("allure-results/environment.properties", None),
    "allure-executor.json": ("allure-results/executor.json", None),
    "junit.xml": ("junit-sample.xml", None),
    "run-trust.txt": (f"run-trust-{LANE}.txt", None),
    "run-trust.excerpt.json": (f"run-trust-{LANE}.json", excerpt_json),
    "verdicts.jsonl": ("verdicts/verdicts.jsonl", _sorted_jsonl),
    "verdict-status.md": ("verdicts/verdict-status.md", None),
    "case-links.json": (f"traceability/case-links-{LANE}.json", None),
    "failure-bundle.excerpt.json": ("failures/*.json", failure_bundle),
    "flakiness-history.excerpt.jsonl": ("flakiness/history.jsonl", _sorted_jsonl),
    "results-bundle.excerpt.ndjson": ("results/bundle-*.ndjson", excerpt_ndjson),
}


def generate(out: str | Path) -> list[str]:
    """Run the sample suite and write every sample into ``out``; returns the file names."""
    target = Path(out)
    with tempfile.TemporaryDirectory(prefix="artefact-samples-") as tmp:
        run_dir = Path(tmp)
        reports = run_sample_suite(run_dir)
        normalise = Normaliser(run_dir)
        files: dict[str, str] = {}
        for name, text in _allure_results(reports).items():
            files[name] = normalise(text + "\n")
        for name, (pattern, transform) in SAMPLES.items():
            # Normalise first, so a sort or trim never depends on a timing value.
            text = normalise(_one(reports, pattern).read_text(encoding="utf-8"))
            files[name] = transform(text) if transform else text
        if target.exists():
            shutil.rmtree(target)
        for name in sorted(files):
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(normalise(files[name]), encoding="utf-8")
    return sorted(files)


def differences(committed: Path, fresh: Path) -> list[str]:
    def listing(base: Path) -> set[str]:
        return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}

    left, right = listing(committed), listing(fresh)
    problems = [f"missing from the committed samples: {n}" for n in sorted(right - left)]
    problems += [f"no longer produced: {n}" for n in sorted(left - right)]
    for name in sorted(left & right):
        if not filecmp.cmp(committed / name, fresh / name, shallow=False):
            problems.append(f"differs from a fresh run: {name}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reporting.artefact_samples")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true", help="regenerate to a temp dir and diff")
    args = parser.parse_args(argv)
    try:
        if not args.check:
            names = generate(args.out)
            print(f"artefact samples: wrote {len(names)} file(s) to {args.out}")
            return 0
        with tempfile.TemporaryDirectory(prefix="artefact-check-") as tmp:
            generate(Path(tmp) / "samples")
            problems = differences(Path(args.out), Path(tmp) / "samples")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"artefact samples: the sample run broke: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    if not problems:
        print("artefact samples: up to date")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
