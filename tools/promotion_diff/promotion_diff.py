"""What promoting one environment's versions onto another would change, per service.

Given two version manifests (each maps service -> git ref deployed there) and
local clones of the services, the report lists for every service:

* the commits FROM has that TO lacks: what the promotion ships, with ticket
  keys pulled out by a configurable regex and housekeeping split off as noise;
* the inversion check: commits only TO has (typically hotfixes made straight on
  the target) that the promotion would REGRESS. A TO-only commit is not a
  regression when FROM already carries it as the same patch (patch-id), under
  the same subject, or with identical content in every file it touched;
* for each ticket key, the test files under ``--test-root`` that mention it.
  A mention is not an assertion: "mentioned" is all that column claims.

Manifest format (JSON or YAML)::

    services:
      orders: v1.4.2                       # clone at <workspace>/orders
      billing: {ref: 3f2c1e0, repo: ../billing-service}

A flat ``{service: ref}`` mapping works too.

Exit status: 0 report written; 1 bad input; 2 with ``--strict`` when a
promotion would regress a commit, or a service pinned in both manifests could
not be compared (an unmeasured service is not a clean one).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_NOISE_FILE = Path(__file__).with_name("noise_patterns.txt")
DEFAULT_KEY_PATTERN = r"\b[A-Z][A-Z0-9]{1,9}-\d+\b"
WORKSPACE_ENV = "PROMOTION_DIFF_WORKSPACE"
_TEXT_SUFFIXES = (".py", ".java", ".kt", ".ts", ".js", ".feature", ".md", ".yml", ".yaml", ".json")


class InputError(Exception):
    """Bad manifest, missing file or invalid option: exit 1."""


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pin:
    service: str
    ref: str
    repo: str | None = None


def load_pins(path: str | Path) -> dict[str, Pin]:
    source = Path(path)
    if not source.is_file():
        raise InputError(f"manifest not found: {source}")
    return parse_pins(source.read_text(encoding="utf-8"), source)


def parse_pins(text: str, source: Path) -> dict[str, Pin]:
    """A manifest's text; ``source`` gives the format (by suffix) and the error label."""
    if source.suffix in (".yml", ".yaml"):
        import yaml  # project dependency; only YAML manifests need it

        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(f"{source}: not valid JSON ({exc})") from None
    if isinstance(data, dict) and isinstance(data.get("services"), dict):
        data = data["services"]
    if not isinstance(data, dict):
        raise InputError(f"{source}: expected a mapping of service to ref")
    pins = {}
    for service, value in data.items():
        if isinstance(value, dict):
            ref, repo = value.get("ref"), value.get("repo")
        else:
            ref, repo = value, None
        if not isinstance(ref, (str, int)) or not str(ref).strip():
            raise InputError(f"{source}: service {service!r} has no ref")
        pins[str(service)] = Pin(str(service), str(ref).strip(), repo)
    return pins


def load_noise(path: str | Path) -> list[re.Pattern[str]]:
    patterns = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error as exc:
            raise InputError(f"{path}:{number}: bad pattern {line!r} ({exc})") from None
    return patterns


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str

    @property
    def short(self) -> str:
        return self.sha[:8]


class Git:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def out(self, *args: str) -> str:
        return self.run(*args).stdout

    def exists(self) -> bool:
        return (self.repo / ".git").exists() and self.run("rev-parse", check=False).returncode == 0

    def fetch(self) -> str | None:
        if not self.out("remote").strip():
            return None
        done = self.run("fetch", "--quiet", "--tags", check=False)
        return None if done.returncode == 0 else done.stderr.strip()

    def resolve(self, ref: str) -> str | None:
        for candidate in (ref, f"refs/tags/{ref}", f"origin/{ref}"):
            done = self.run(
                "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}", check=False
            )
            if done.returncode == 0:
                return done.stdout.strip()
        return None

    def commits(self, rev_range: str) -> list[Commit]:
        lines = self.out("log", "--no-merges", "--format=%H%x1f%s", rev_range).splitlines()
        return [Commit(*line.split("\x1f", 1)) for line in lines if "\x1f" in line]

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.run("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def cherry(self, upstream: str, head: str) -> list[tuple[bool, str]]:
        """``(has_equivalent_patch_upstream, sha)`` for each commit of head not in upstream."""
        rows = []
        for line in self.out("cherry", upstream, head).splitlines():
            sign, _, sha = line.partition(" ")
            rows.append((sign == "-", sha.strip()))
        return rows

    def files_of(self, sha: str) -> list[str]:
        return [
            f
            for f in self.out("show", "--no-renames", "--name-only", "--format=", sha).splitlines()
            if f
        ]

    def blob(self, rev: str, path: str) -> str | None:
        done = self.run("rev-parse", "--verify", "--quiet", f"{rev}:{path}", check=False)
        return done.stdout.strip() if done.returncode == 0 else None

    def tags_containing(self, sha: str) -> list[str]:
        return self.out("tag", "--contains", sha).split()


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


@dataclass
class Inverted:
    commit: Commit
    equivalence: str | None  # None => the promotion would regress it
    tags: list[str] = field(default_factory=list)


@dataclass
class ServiceDelta:
    service: str
    from_ref: str | None
    to_ref: str | None
    problem: str | None = None
    commits: list[Commit] = field(default_factory=list)
    inverted: list[Inverted] = field(default_factory=list)

    @property
    def regressions(self) -> list[Inverted]:
        return [i for i in self.inverted if i.equivalence is None]


def keys_in(text: str, key_re: re.Pattern[str]) -> list[str]:
    return sorted(set(key_re.findall(text)))


def is_noise(subject: str, noise: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(subject) for p in noise)


def split_commits(
    commits: Sequence[Commit], key_re: re.Pattern[str], noise: Sequence[re.Pattern[str]]
) -> tuple[dict[str, list[Commit]], list[Commit], list[Commit]]:
    """A commit naming a key is a change for that key; otherwise noise or unkeyed."""
    keyed: dict[str, list[Commit]] = {}
    unkeyed, noisy = [], []
    for commit in commits:
        keys = keys_in(commit.subject, key_re)
        if keys:
            for key in keys:
                keyed.setdefault(key, []).append(commit)
        elif is_noise(commit.subject, noise):
            noisy.append(commit)
        else:
            unkeyed.append(commit)
    return keyed, unkeyed, noisy


def check_inversion(git: Git, from_sha: str, to_sha: str) -> list[Inverted]:
    """Commits only TO has, each with the equivalence that clears it (or None)."""
    if git.is_ancestor(to_sha, from_sha):
        return []
    from_subjects = {c.subject.strip() for c in git.commits(f"{to_sha}..{from_sha}")}
    subjects = {c.sha: c for c in git.commits(f"{from_sha}..{to_sha}")}
    result = []
    for patch_equal, sha in git.cherry(from_sha, to_sha):
        commit = subjects.get(sha)
        if commit is None:  # a merge commit: log --no-merges left it out
            continue
        if patch_equal:
            reason = "same patch"
        elif commit.subject.strip() in from_subjects:
            reason = "same subject"
        else:
            files = git.files_of(sha)
            if not files:
                reason = "empty commit"
            elif not any(git.blob(from_sha, path) != git.blob(to_sha, path) for path in files):
                reason = "identical files"
            else:
                reason = None
        tags = git.tags_containing(sha) if reason is None else []
        result.append(Inverted(commit, reason, tags))
    return result


def analyse_service(
    workspace: Path, service: str, from_pin: Pin | None, to_pin: Pin | None, fetch: bool
) -> ServiceDelta:
    delta = ServiceDelta(service, from_pin and from_pin.ref, to_pin and to_pin.ref)
    if from_pin is None:
        delta.problem = "not in FROM: the promotion would not touch it"
        return delta
    repo = workspace / (from_pin.repo or (to_pin and to_pin.repo) or service)
    git = Git(repo)
    if not git.exists():
        delta.problem = f"no git clone at {repo}"
        return delta
    if fetch:
        warning = git.fetch()
        if warning:
            print(f"warning: fetch failed for {service}: {warning}", file=sys.stderr)
    from_sha = git.resolve(from_pin.ref)
    if from_sha is None:
        delta.problem = f"FROM ref {from_pin.ref!r} not found in {repo}"
        return delta
    if to_pin is None:
        delta.problem = "not in TO: new service, every commit up to FROM is new"
        return delta
    to_sha = git.resolve(to_pin.ref)
    if to_sha is None:
        delta.problem = f"TO ref {to_pin.ref!r} not found in {repo}"
        return delta
    delta.commits = git.commits(f"{to_sha}..{from_sha}")
    delta.inverted = check_inversion(git, from_sha, to_sha)
    return delta


def coverage_index(roots: Sequence[str | Path], key_re: re.Pattern[str]) -> dict[str, list[str]]:
    """Which test files mention which keys. A mention, not an assertion."""
    index: dict[str, set[str]] = {}
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            raise InputError(f"test root not found: {base}")
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for key in keys_in(content, key_re):
                index.setdefault(key, set()).add(path.as_posix())
    return {key: sorted(files) for key, files in index.items()}


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(
    deltas: Sequence[ServiceDelta],
    labels: tuple[str, str],
    shown: Sequence[tuple[str, dict[str, Pin]]],
    key_re: re.Pattern[str],
    noise: Sequence[re.Pattern[str]],
    coverage: dict[str, list[str]] | None,
) -> str:
    source, target = labels
    lines = [f"# Promotion diff: {source} -> {target}", ""]
    header = ["Service", source, target, *(name for name, _ in shown), "Commits", "Keys", "Regress"]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for d in deltas:
        keyed, _, _ = split_commits(d.commits, key_re, noise)
        row = [
            d.service,
            d.from_ref or "-",
            d.to_ref or "-",
            *((pins[d.service].ref if d.service in pins else "-") for _, pins in shown),
            "?" if d.problem else str(len(d.commits)),
            "?" if d.problem else str(len(keyed)),
            "?" if d.problem else str(len(d.regressions)),
        ]
        lines.append("| " + " | ".join(_md(cell) for cell in row) + " |")

    for d in deltas:
        lines += ["", f"## {d.service}", ""]
        if d.problem:
            lines.append(f"**Not compared:** {d.problem}")
            continue
        if not d.commits and not d.inverted:
            lines.append("No change: both environments run the same code.")
            continue
        keyed, unkeyed, noisy = split_commits(d.commits, key_re, noise)
        if keyed:
            lines.append(f"Ships {len(keyed)} key(s):")
            for key in sorted(keyed):
                subjects = "; ".join(f"{c.subject} ({c.short})" for c in keyed[key])
                tests = ""
                if coverage is not None:
                    found = coverage.get(key)
                    tests = (
                        f" (tests mentioning it: {', '.join(found)})"
                        if found
                        else " (no test mentions it)"
                    )
                lines.append(f"- {key}: {_md(subjects)}{tests}")
        if unkeyed:
            lines.append(f"Unkeyed changes ({len(unkeyed)}):")
            lines += [f"- {_md(c.subject)} ({c.short})" for c in unkeyed]
        if noisy:
            lines.append(f"Noise: {len(noisy)} commit(s) matching the noise patterns.")
        regressions = d.regressions
        if regressions:
            lines.append(f"**Would regress {len(regressions)} commit(s) only {target} has:**")
            for inv in regressions:
                tags = f" (in tags: {', '.join(inv.tags)})" if inv.tags else ""
                lines.append(f"- {_md(inv.commit.subject)} ({inv.commit.short}){tags}")
        cleared = [i for i in d.inverted if i.equivalence]
        if cleared:
            lines.append(f"{target}-only commits already carried by {source}:")
            lines += [
                f"- {_md(i.commit.subject)} ({i.commit.short}): {i.equivalence}" for i in cleared
            ]
    return "\n".join(lines) + "\n"


def collect_keys(deltas: Sequence[ServiceDelta], key_re: re.Pattern[str]) -> list[str]:
    keys: set[str] = set()
    for d in deltas:
        for commit in d.commits:
            keys.update(keys_in(commit.subject, key_re))
    return sorted(keys)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="promotion_diff", description="What promoting FROM onto TO would change."
    )
    p.add_argument("--from", dest="source", required=True, help="manifest of the source env")
    p.add_argument("--to", dest="target", required=True, help="manifest of the target env")
    p.add_argument(
        "--show", action="append", default=[], help="extra manifest shown as a column (repeatable)"
    )
    p.add_argument(
        "--workspace",
        default=os.environ.get(WORKSPACE_ENV, "."),
        help=f"directory holding the service clones (default ${WORKSPACE_ENV} or .)",
    )
    p.add_argument("--noise-file", default=str(DEFAULT_NOISE_FILE))
    p.add_argument("--key-pattern", default=DEFAULT_KEY_PATTERN, help="regex for ticket keys")
    p.add_argument("--test-root", action="append", default=[], help="where tests live (repeatable)")
    p.add_argument(
        "--no-fetch", action="store_true", help="skip git fetch and work from local refs"
    )
    p.add_argument("--keys", action="store_true", help="list the shipped keys and nothing else")
    p.add_argument("--strict", action="store_true", help="exit 2 on a regression or a gap")
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        key_re = re.compile(args.key_pattern)
        source, target = load_pins(args.source), load_pins(args.target)
        shown = [(Path(s).stem, load_pins(s)) for s in args.show]
        noise = load_noise(args.noise_file)
        coverage = coverage_index(args.test_root, key_re) if args.test_root else None
    except (InputError, re.error, OSError) as exc:
        print(f"promotion_diff: {exc}", file=sys.stderr)
        return 1
    workspace = Path(args.workspace)
    deltas = [
        analyse_service(workspace, name, source.get(name), target.get(name), not args.no_fetch)
        for name in sorted(set(source) | set(target))
    ]
    if args.keys:
        text = "\n".join(collect_keys(deltas, key_re)) + "\n"
    else:
        labels = (Path(args.source).stem, Path(args.target).stem)
        text = render(deltas, labels, shown, key_re, noise, coverage)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    gap = any(d.problem and d.from_ref and d.to_ref for d in deltas)
    if args.strict and (gap or any(d.regressions for d in deltas)):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
