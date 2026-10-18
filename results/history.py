"""Durable history across ephemeral CI runners: run bundles on a ``results-history`` branch.

A CI runner's SQLite file dies with the job. The history therefore lives as compressed
run bundles in a directory that CI keeps on an orphan git branch (``results-history``),
and the database is only a cache rebuilt from them:

    <history dir>/
        README.md
        bundles/<YYYY-MM>/bundle-<run_id>.ndjson.gz

This module only reads and writes that directory; it never runs git. Checking the branch
out, committing and pushing are CI steps (see ``docs/features/results-history-and-trends.md``).
Bundles are gzip-compressed with a fixed timestamp, so the same run always produces the
same bytes and a re-add is not a git change.
"""

from __future__ import annotations

import gzip
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from results.bundle import BundleError, import_bundle, read_bundle, safe_name, write_bundle

BRANCH = "results-history"
DEFAULT_DIR = Path(".results-history")
BUNDLES = "bundles"
README = """# results-history

Machine-written branch: one gzip-compressed NDJSON run bundle per test run, under
`bundles/<YYYY-MM>/`. CI rebuilds the results database from these files with
`python -m results history rebuild --dir <checkout>`; the database is a cache, these
bundles are the record. Do not edit files by hand: a bundle is imported as-is.
"""


@dataclass
class RebuildReport:
    imported: list[str] = field(default_factory=list)  # bundle paths
    runs: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"imported": len(self.imported), "runs": self.runs, "failed": self.failed}


def _month(started_at: str | None) -> str:
    return (started_at or "unknown")[:7] if started_at and len(started_at) >= 7 else "unknown"


def bundle_location(history_dir: Path, run_id: str, started_at: str | None) -> Path:
    name = f"bundle-{safe_name(run_id)}.ndjson.gz"
    return Path(history_dir) / BUNDLES / _month(started_at) / name


def _ensure_readme(history_dir: Path) -> None:
    readme = Path(history_dir) / "README.md"
    if not readme.exists():
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(README, encoding="utf-8")


def add_run(store: Any, run_id: str, history_dir: Path = DEFAULT_DIR) -> Path:
    """Write one stored run's bundle into the history directory."""
    run = store.run(run_id)
    if run is None:
        from results.store import StoreError

        raise StoreError(f"unknown run {run_id!r}")
    _ensure_readme(history_dir)
    return write_bundle(store, run_id, bundle_location(history_dir, run_id, run.started_at))


def add_bundle_file(bundle: Path, history_dir: Path = DEFAULT_DIR) -> Path:
    """Copy an existing bundle file (plain or .gz) into the history layout, compressed."""
    first = next(iter(read_bundle(bundle)), None)
    if first is None or first.type != "run":
        raise BundleError(1, "a history bundle must start with its run record")
    target = bundle_location(history_dir, first.data["run_id"], first.data.get("started_at"))
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_readme(history_dir)
    if Path(bundle).suffix == ".gz":
        shutil.copyfile(bundle, target)
        return target
    with gzip.GzipFile(target, "wb", mtime=0) as handle:
        handle.write(Path(bundle).read_bytes())
    return target


def history_bundles(history_dir: Path = DEFAULT_DIR) -> list[Path]:
    root = Path(history_dir) / BUNDLES
    if not root.is_dir():
        return []
    return sorted(root.glob("*/bundle-*.ndjson*"))


def rebuild(store: Any, history_dir: Path = DEFAULT_DIR) -> RebuildReport:
    """Import every bundle of the history into ``store`` (idempotent).

    A corrupt bundle is reported, not fatal: one bad file must not hide the rest of the
    history, and it must not pass unnoticed either (the CLI exits non-zero).
    """
    report = RebuildReport()
    for path in history_bundles(history_dir):
        try:
            result = import_bundle(store, path)
        except Exception as exc:
            report.failed.append({"bundle": str(path), "error": str(exc)})
            continue
        report.imported.append(str(path))
        report.runs.extend(r for r in result.run_ids if r not in report.runs)
    return report


def prune_history(history_dir: Path = DEFAULT_DIR, *, keep_runs: int) -> list[Path]:
    """Delete all but the newest ``keep_runs`` bundles (by run start); returns removed files."""
    dated: list[tuple[str, Path]] = []
    for path in history_bundles(history_dir):
        try:
            first = next(iter(read_bundle(path, validate=False)))
            started = str(first.data.get("started_at") or "")
        except Exception:
            started = ""
        dated.append((started, path))
    dated.sort()
    doomed = [p for _, p in dated[: max(len(dated) - max(keep_runs, 0), 0)]]
    for path in doomed:
        path.unlink()
    return doomed


__all__ = [
    "BRANCH",
    "DEFAULT_DIR",
    "RebuildReport",
    "add_bundle_file",
    "add_run",
    "bundle_location",
    "history_bundles",
    "prune_history",
    "rebuild",
]
