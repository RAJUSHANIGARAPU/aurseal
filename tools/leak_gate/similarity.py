"""Fail when this repo carries a run of >= 10 near-identical lines from a private tree.

``build`` reads a private source tree and writes ONLY winnowed k-gram hashes
(see ``fingerprint.py``), one record per file, to a file OUTSIDE this repo:
default ``~/.config/leak-gate/fingerprints.bin``, overridden by
``LEAK_GATE_FINGERPRINTS``. No path, name or text of the private tree is
stored; files are numbered in walk order.

``check`` tokenizes this repo's files the same way and hashes EVERY k-gram
(not just the winnowed ones, so any kept private fingerprint is found). A
line is matched when a k-gram whose hash is a private fingerprint covers it;
runs of matched code lines are formed as described in ``fingerprint.runs``.
A run of ``--min-lines`` (default 10) matched code lines fails; in diff modes
only a run that touches an added line fails. Findings print this repo's
``path:start-end`` and the private file NUMBER, never private text.

Modes::

    python -m tools.leak_gate.similarity build /path/to/private/tree
    python -m tools.leak_gate.similarity check --staged
    python -m tools.leak_gate.similarity check --range BASE..HEAD
    python -m tools.leak_gate.similarity check --tree
    python -m tools.leak_gate.similarity check --tree --paths a.py b.py

Exit status: 0 clean; 1 findings; 2 fingerprints missing or unreadable.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.leak_gate import fingerprint as fp
from tools.leak_gate import gitsrc

ENV_VAR = "LEAK_GATE_FINGERPRINTS"
DEFAULT_PATH = Path("~/.config/leak-gate/fingerprints.bin")
MAGIC = b"LGFP"
FORMAT_VERSION = 1
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = frozenset({".git", "node_modules", "target", "build", ".venv", "__pycache__"})
MIN_LINES = 10
MAX_GAP = 2


class FingerprintsUnavailable(Exception):
    """The fingerprint store cannot be loaded; the gate must not pass."""


def store_path() -> Path:
    return Path(os.environ.get(ENV_VAR) or DEFAULT_PATH).expanduser()


# --- store -----------------------------------------------------------------


def write_store(path: Path, per_file: Sequence[set[int]]) -> None:
    """Header (magic, version, K, W, file count), then per file: count + sorted u64s."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as out:
        out.write(MAGIC + struct.pack(">HHHI", FORMAT_VERSION, fp.K, fp.W, len(per_file)))
        for hashes in per_file:
            ordered = sorted(hashes)
            out.write(struct.pack(">I", len(ordered)))
            out.write(struct.pack(f">{len(ordered)}Q", *ordered))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_store(path: Path) -> dict[int, int]:
    """Map fingerprint -> number of the first private file that holds it."""
    if not path.is_file():
        raise FingerprintsUnavailable(
            f"fingerprints not found at {path}. Generate them with "
            f"`python -m tools.leak_gate.similarity build <private-tree>` or set {ENV_VAR}. "
            "The gate fails closed without them."
        )
    data = path.read_bytes()
    header = len(MAGIC) + struct.calcsize(">HHHI")
    if len(data) < header or not data.startswith(MAGIC):
        raise FingerprintsUnavailable(f"{path} is not a leak-gate fingerprint file")
    version, k, w, files = struct.unpack(">HHHI", data[len(MAGIC) : header])
    if (version, k, w) != (FORMAT_VERSION, fp.K, fp.W):
        raise FingerprintsUnavailable(
            f"{path} was built with format {version}, K={k}, W={w}; this checker needs "
            f"format {FORMAT_VERSION}, K={fp.K}, W={fp.W}. Rebuild it."
        )
    index: dict[int, int] = {}
    offset = header
    try:
        for number in range(1, files + 1):
            (count,) = struct.unpack_from(">I", data, offset)
            offset += 4
            for h in struct.unpack_from(f">{count}Q", data, offset):
                index.setdefault(h, number)
            offset += 8 * count
    except struct.error as exc:
        raise FingerprintsUnavailable(f"{path} is truncated ({exc})") from exc
    if not index:
        raise FingerprintsUnavailable(f"{path} holds no fingerprints; refusing to pass")
    return index


# --- build -----------------------------------------------------------------


def iter_text_files(root: Path) -> Iterable[tuple[Path, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            blob = path.read_bytes()
            if b"\0" in blob[:8192]:
                continue
            yield path, blob.decode("utf-8", errors="replace")


def build(source: Path, out: Path) -> tuple[int, int]:
    per_file = [fp.fingerprint_text(text) for _, text in iter_text_files(source)]
    per_file = [hashes for hashes in per_file if hashes]
    write_store(out, per_file)
    return len(per_file), sum(len(h) for h in per_file)


# --- check -----------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    path: str
    run: fp.Run
    private_files: tuple[int, ...]


def check_file(
    f: gitsrc.ScanFile,
    private: dict[int, int],
    min_lines: int = MIN_LINES,
    max_gap: int = MAX_GAP,
) -> list[Finding]:
    if len(f.text) > MAX_FILE_BYTES:
        return []
    tokens = fp.tokenize(f.text)
    hashes = fp.kgram_hashes(tokens)
    matched: set[int] = set()
    sources_by_line: dict[int, set[int]] = {}
    for i, h in enumerate(hashes):
        number = private.get(h)
        if number is None:
            continue
        for line in range(tokens[i].line, tokens[i + fp.K - 1].line + 1):
            matched.add(line)
            sources_by_line.setdefault(line, set()).add(number)
    findings = []
    for run in fp.runs(matched, fp.code_lines(tokens), min_lines, max_gap):
        span = range(run.start, run.end + 1)
        if f.added is not None and not any(line in f.added for line in span):
            continue
        numbers = sorted({n for line in span for n in sources_by_line.get(line, ())})
        findings.append(Finding(f.path, run, tuple(numbers)))
    return findings


def _files(args: argparse.Namespace, repo: Path) -> Iterable[gitsrc.ScanFile]:
    if args.staged:
        return gitsrc.staged(repo)
    if args.range:
        return gitsrc.commit_range(repo, args.range)
    files = gitsrc.tree(repo)
    if args.paths:
        wanted = set(args.paths)
        files = (f for f in files if f.path in wanted)
    return files


def run_check(args: argparse.Namespace) -> int:
    try:
        private = read_store(store_path())
    except FingerprintsUnavailable as exc:
        print(f"LEAK GATE (similarity): CANNOT RUN: {exc}", file=sys.stderr)
        return 2
    repo = gitsrc.repo_root(Path(args.repo))
    findings = [
        finding
        for f in _files(args, repo)
        for finding in check_file(f, private, args.min_lines, args.max_gap)
    ]
    for f in findings:
        shown = ", ".join(f"#{n}" for n in f.private_files[:5])
        more = "" if len(f.private_files) <= 5 else f" (+{len(f.private_files) - 5})"
        print(
            f"{f.path}:{f.run.start}-{f.run.end}: {f.run.matched} near-identical code lines "
            f"(private file {shown}{more})"
        )
    if findings:
        print(
            f"LEAK GATE (similarity): FAIL, {len(findings)} run(s) of >= {args.min_lines} "
            "lines match the private tree. Rewrite from the behaviour, not the source.",
            file=sys.stderr,
        )
        return 1
    print("LEAK GATE (similarity): clean")
    return 0


def run_build(args: argparse.Namespace) -> int:
    source = Path(args.source)
    if not source.is_dir():
        print(f"not a directory: {source}", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser() if args.out else store_path()
    files, hashes = build(source, out)
    print(f"wrote {hashes} fingerprints from {files} files to {out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="fingerprint a private source tree")
    b.add_argument("source", help="directory to fingerprint (read only)")
    b.add_argument("--out", help=f"output file (default: ${ENV_VAR} or {DEFAULT_PATH})")
    b.set_defaults(func=run_build)
    c = sub.add_parser("check", help="check this repo against the fingerprints")
    mode = c.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--range", metavar="BASE..HEAD")
    mode.add_argument("--tree", action="store_true")
    c.add_argument("--paths", nargs="+", help="with --tree: only these repo paths")
    c.add_argument("--repo", default=".")
    c.add_argument("--min-lines", type=int, default=MIN_LINES)
    c.add_argument("--max-gap", type=int, default=MAX_GAP)
    c.set_defaults(func=run_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
