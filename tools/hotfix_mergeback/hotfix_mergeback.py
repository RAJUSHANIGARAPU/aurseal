"""Which fixes run in production that the next release from the default branch would drop.

A unit of work is one ``(service, tag)``: a production or hotfix tag of a
service. For each one the report takes the commits from a BASE to the tag and
asks, fix by fix, whether the default branch already carries them.

* **Base.** A hotfix tag (``--hotfix-pattern``, default ``vX.Y.Z-hotfix-N``)
  whose release tag ``vX.Y.Z`` exists starts from that tag; hotfix tags are
  cumulative, so ``-hotfix-2`` also carries what ``-hotfix-1`` carried. Any
  other tag starts from its merge-base with the default branch.
* **Dropped before matching.** A commit and its revert inside the range cancel
  out. A commit whose every touched file matches the non-product globs (CI
  configuration, dependency manifests and lock files, docs, tests) is
  NOT-NEEDED: the product loses nothing without it.
* **Matching.** The rest is grouped per ticket key (a commit naming no key is a
  unit of its own) and each unit goes through a chain, first hit wins:

  ===============  ============================================================
  same commit      every commit of the unit is an ancestor of the default branch
  patch-id         (unkeyed only) the same patch-id on default
  key / subject    a commit in base..default names the key (a merge commit is
                   preferred as evidence: a back-port is a new commit, so its
                   patch-id differs); unkeyed: the same normalised subject
  content          every commit's diff applies in reverse to the default tree
                   (``git apply --reverse --check``): the fix is already there,
                   under another ticket or none
  ===============  ============================================================

Verdict per ``(service, tag)``: MISSING (a unit was not found), BACK-PORTED (all
found), NOT-NEEDED (nothing remained to match), ERROR (no clone, tag or default
branch). Each found unit names its evidence commit.

Exit status: 0 report written (report-only by default); 1 bad input; 2 with
``--fail-on-missing`` when a verdict is MISSING or ERROR (an unchecked tag is
not a clean one).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tools.promotion_diff.promotion_diff import (
    DEFAULT_KEY_PATTERN,
    Git,
    InputError,
    _md,
    keys_in,
    load_pins,
)

DEFAULT_HOTFIX_PATTERN = r"^(?P<base>v?\d+\.\d+\.\d+)-hotfix-\d+$"
DEFAULT_NON_PRODUCT_FILE = Path(__file__).with_name("non_product_globs.txt")
VERDICTS = ("MISSING", "ERROR", "BACK-PORTED", "NOT-NEEDED")
_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


def parse_time(value: str, where: str) -> datetime:
    """ISO 8601; a value without an offset is taken as UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise InputError(f"{where}: not an ISO 8601 time: {value!r}") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def load_globs(path: str | Path) -> list[str]:
    source = Path(path)
    if not source.is_file():
        raise InputError(f"non-product glob file not found: {source}")
    lines = (raw.strip() for raw in source.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def key_regex(prefixes: Sequence[str], pattern: str) -> re.Pattern[str]:
    """``--key-prefix`` values win over ``--key-pattern``."""
    if prefixes:
        return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in prefixes) + r")-\d+\b")
    return re.compile(pattern)


@dataclass(frozen=True)
class Target:
    """One ``(service, tag)`` to check; ``repo`` is the clone directory name."""

    service: str
    tag: str
    repo: str


def parse_target(value: str) -> Target:
    repo, sep, tag = value.partition(":")
    if not sep or not repo.strip() or not tag.strip():
        raise InputError(f"--tag {value!r}: expected REPO:TAG")
    return Target(repo.strip(), tag.strip(), repo.strip())


def manifest_targets(path: str, hotfix_re: re.Pattern[str] | None) -> list[Target]:
    """Every pin, or with ``hotfix_re`` only the pins that are hotfix versions."""
    pins = load_pins(path).values()
    return [
        Target(p.service, p.ref, p.repo or p.service)
        for p in pins
        if hotfix_re is None or hotfix_re.match(p.ref)
    ]


def origin_name(git: Git) -> str | None:
    done = git.run("remote", "get-url", "origin", check=False)
    if done.returncode != 0:
        return None
    url = done.stdout.strip().rstrip("/")
    name = re.split(r"[/:]", url)[-1]
    return name[:-4] if name.endswith(".git") else name


def window_targets(
    workspace: Path,
    hotfix_re: re.Pattern[str],
    since: datetime | None,
    until: datetime | None,
) -> tuple[list[Target], list[str]]:
    """Hotfix tags created in ``[since, until)`` across the clones; skipped clones named."""
    targets, skipped = [], []
    for repo in sorted(p for p in workspace.iterdir() if p.is_dir()):
        git = Git(repo)
        if not git.exists():
            continue
        origin = origin_name(git)
        if origin is not None and origin != repo.name:
            skipped.append(f"{repo.name} (a copy of {origin})")
            continue
        out = git.out(
            "for-each-ref", "--format=%(refname:short)%1f%(creatordate:iso-strict)", "refs/tags"
        )
        for line in out.splitlines():
            tag, at = line.split("\x1f")
            if not hotfix_re.match(tag):
                continue
            created = parse_time(at, tag)
            if (since and created < since) or (until and created >= until):
                continue
            targets.append(Target(repo.name, tag, repo.name))
    return targets, skipped


def ensure_clone(workspace: Path, repo: str, clone_base: str | None) -> str | None:
    """Blobless clone of a missing service; an error message when that fails."""
    if clone_base is None or (workspace / repo / ".git").exists():
        return None
    url = f"{clone_base.rstrip('/')}/{repo}"
    done = subprocess.run(
        ["git", "clone", "--quiet", "--filter=blob:none", url, str(workspace / repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    return None if done.returncode == 0 else f"clone of {url} failed: {done.stderr.strip()}"


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str = ""
    parents: int = 1

    @property
    def short(self) -> str:
        return self.sha[:8]

    @property
    def message(self) -> str:
        return f"{self.subject}\n{self.body}"


def log(git: Git, rev_range: str, *extra: str) -> list[Commit]:
    out = git.out("log", *extra, "--format=%H%x1f%P%x1f%s%x1f%b%x1e", rev_range)
    commits = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, subject, body = record.split("\x1f", 3)
        commits.append(Commit(sha, subject, body.strip(), len(parents.split())))
    return commits


def resolve_default(git: Git, name: str | None) -> str | None:
    """``name`` if given, else origin/HEAD, then main, then master.

    A remote-tracking branch wins over a local one: a stale local branch would
    misreport.
    """
    candidates = (
        [f"refs/remotes/origin/{name}", name]
        if name
        else [
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
            "main",
            "refs/remotes/origin/master",
            "master",
        ]
    )
    for ref in candidates:
        sha = git.resolve(ref)
        if sha:
            return sha
    return None


def resolve_mainline(git: Git, mainline: str) -> str | None:
    """Kept for callers that name the branch: the remote-tracking one first."""
    return resolve_default(git, mainline)


def files_of(git: Git, sha: str) -> list[str]:
    out = git.out("show", "--no-renames", "--name-only", "--format=", sha)
    return [f for f in out.splitlines() if f]


class ReverseApply:
    """``git apply --reverse --check`` against a tree, through a private index."""

    def __init__(self, git: Git, tree: str) -> None:
        self.git = git
        handle, self.index = tempfile.mkstemp(prefix="mergeback-index-")
        os.close(handle)
        os.unlink(self.index)
        self.env = {**os.environ, "GIT_INDEX_FILE": self.index}
        self._git("read-tree", tree)

    def _git(self, *args: str, patch: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.git.repo), *args],
            input=patch,
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def already_there(self, sha: str) -> bool:
        patch = self.git.out("diff", "--binary", "--full-index", f"{sha}^", sha)
        if not patch.strip():
            return True
        return (
            self._git("apply", "--cached", "--reverse", "--check", "-", patch=patch).returncode == 0
        )

    def close(self) -> None:
        if os.path.exists(self.index):
            os.unlink(self.index)


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def drop_revert_pairs(commits: Sequence[Commit]) -> tuple[list[Commit], list[Commit]]:
    """``(kept, dropped)``; ``commits`` oldest first. A revert of a revert stays."""
    kept: list[Commit] = []
    dropped: list[Commit] = []
    for commit in commits:
        match = _REVERTS.search(commit.body)
        target = None
        if match:
            target = next((c for c in kept if c.sha.startswith(match.group(1))), None)
        if target is not None:
            kept.remove(target)
            dropped += [target, commit]
        else:
            kept.append(commit)
    return kept, dropped


def is_non_product(files: Iterable[str], globs: Sequence[str]) -> bool:
    files = list(files)
    return bool(files) and all(any(fnmatch.fnmatch(f, g) for g in globs) for f in files)


_PREFIXES = re.compile(
    r"^(?:\[?(?:cherry[- ]?pick(?:ed)?|back-?port(?:ed)?|hotfix)\]?:?\s*)+", re.IGNORECASE
)
_CONVENTIONAL = re.compile(r"^[a-z]+(?:\([^)]*\))?!?:\s*", re.IGNORECASE)


def normalise_subject(subject: str) -> str:
    """Lower case, without back-port markers, a conventional prefix, a PR number."""
    text = subject.strip()
    text = re.sub(r"\s*\((?:#\d+|cherry picked from[^)]*)\)\s*$", "", text, flags=re.IGNORECASE)
    text = _PREFIXES.sub("", text)
    text = _CONVENTIONAL.sub("", text)
    text = _PREFIXES.sub("", text)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass
class Unit:
    name: str  # the ticket key, or the subject of an unkeyed commit
    keyed: bool
    commits: list[Commit] = field(default_factory=list)
    method: str | None = None  # None => MISSING
    evidence: Commit | None = None


@dataclass
class TagReport:
    service: str
    tag: str
    repo: str
    base: str | None = None
    base_kind: str | None = None  # "release tag" or "merge-base"
    default: str | None = None
    problem: str | None = None
    units: list[Unit] = field(default_factory=list)
    reverted: list[Commit] = field(default_factory=list)
    not_needed: list[Commit] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.problem:
            return "ERROR"
        if not self.units:
            return "NOT-NEEDED"
        return "MISSING" if any(u.method is None for u in self.units) else "BACK-PORTED"

    @property
    def missing(self) -> list[Unit]:
        return [u for u in self.units if u.method is None]


@dataclass(frozen=True)
class Settings:
    workspace: Path
    default_branch: str | None
    hotfix_re: re.Pattern[str]
    key_re: re.Pattern[str]
    globs: Sequence[str]
    fetch: bool
    clone_base: str | None


def group_units(commits: Sequence[Commit], key_re: re.Pattern[str]) -> list[Unit]:
    units: dict[str, Unit] = {}
    for commit in commits:
        keys = keys_in(commit.message, key_re)
        if not keys:
            units.setdefault(f"\0{commit.sha}", Unit(commit.subject, False)).commits.append(commit)
        for key in keys:
            units.setdefault(key, Unit(key, True)).commits.append(commit)
    return sorted(units.values(), key=lambda u: (not u.keyed, u.name))


def match_unit(
    git: Git,
    unit: Unit,
    default_sha: str,
    on_default: Sequence[Commit],
    patches_on_default: dict[str, str],
    key_re: re.Pattern[str],
    content: ReverseApply,
) -> None:
    """Fill ``unit.method`` and ``unit.evidence`` with the first link of the chain that holds."""
    if all(git.is_ancestor(c.sha, default_sha) for c in unit.commits):
        unit.method, unit.evidence = "same commit", unit.commits[-1]
        return
    by_sha = {c.sha: c for c in on_default}
    if not unit.keyed:
        same = patches_on_default.get(patch_id(git, unit.commits[0].sha) or "")
        if same:
            unit.method, unit.evidence = "patch-id", by_sha.get(same)
            return
    if unit.keyed:
        hits = [c for c in on_default if unit.name in keys_in(c.message, key_re)]
        method = "key"
    else:
        wanted = normalise_subject(unit.name)
        hits = [c for c in on_default if wanted and normalise_subject(c.subject) == wanted]
        method = "subject"
    if hits:
        merges = [c for c in hits if c.parents > 1]
        unit.method, unit.evidence = method, (merges or hits)[0]  # oldest; a merge preferred
        return
    if all(content.already_there(c.sha) for c in unit.commits):
        unit.method = "content"


def _patch_ids(git: Git, *log_args: str) -> list[tuple[str, str]]:
    """``(patch_id, sha)`` pairs, through ``git patch-id --stable``."""
    patch = git.out(*log_args)
    done = subprocess.run(
        ["git", "-C", str(git.repo), "patch-id", "--stable"],
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )
    return [tuple(line.split()[:2]) for line in done.stdout.splitlines() if line.strip()]


def patch_id(git: Git, sha: str) -> str | None:
    pairs = _patch_ids(git, "show", "--format=commit %H", sha)
    return pairs[0][0] if pairs else None


def patch_ids(git: Git, rev_range: str) -> dict[str, str]:
    """patch-id -> the oldest non-merge commit in ``rev_range`` with that patch."""
    pairs = _patch_ids(
        git, "log", "--no-merges", "--reverse", "-p", "--format=commit %H", rev_range
    )
    found: dict[str, str] = {}
    for pid, sha in pairs:
        found.setdefault(pid, sha)
    return found


def find_base(git: Git, tag: str, tag_sha: str, default_sha: str, hotfix_re) -> tuple[str, str]:
    match = hotfix_re.match(tag)
    if match and "base" in match.groupdict():
        release = git.resolve(f"refs/tags/{match.group('base')}")
        if release:
            return release, f"release tag {match.group('base')}"
    base = git.out("merge-base", tag_sha, default_sha).strip()
    return base, "merge-base"


def analyse(target: Target, s: Settings) -> TagReport:
    report = TagReport(target.service, target.tag, target.repo)
    problem = ensure_clone(s.workspace, target.repo, s.clone_base)
    git = Git(s.workspace / target.repo)
    if problem or not git.exists():
        report.problem = problem or f"no git clone at {s.workspace / target.repo}"
        return report
    if s.fetch:
        warning = git.fetch()
        if warning:
            print(f"warning: fetch failed for {target.repo}: {warning}", file=sys.stderr)
    tag_sha = git.resolve(f"refs/tags/{target.tag}") or git.resolve(target.tag)
    if tag_sha is None:
        report.problem = f"tag {target.tag!r} not found in {target.repo}"
        return report
    default_sha = resolve_default(git, s.default_branch)
    if default_sha is None:
        report.problem = (
            f"default branch {s.default_branch or 'origin/HEAD, main, master'} not found"
        )
        return report
    report.default = default_sha
    base, report.base_kind = find_base(git, target.tag, tag_sha, default_sha, s.hotfix_re)
    report.base = base
    commits = log(git, f"{base}..{tag_sha}", "--no-merges", "--reverse")
    commits, report.reverted = drop_revert_pairs(commits)
    product = []
    for commit in commits:
        if is_non_product(files_of(git, commit.sha), s.globs):
            report.not_needed.append(commit)
        else:
            product.append(commit)
    report.units = group_units(product, s.key_re)
    if not report.units:
        return report
    on_default = log(git, f"{base}..{default_sha}", "--reverse")
    patches_on_default = patch_ids(git, f"{base}..{default_sha}")
    content = ReverseApply(git, default_sha)
    try:
        for unit in report.units:
            match_unit(git, unit, default_sha, on_default, patches_on_default, s.key_re, content)
    finally:
        content.close()
    return report


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def headline(reports: Sequence[TagReport]) -> dict[str, int]:
    counts = {v: 0 for v in VERDICTS}
    for r in reports:
        counts[r.verdict] += 1
    counts["missing_units"] = sum(len(r.missing) for r in reports)
    return counts


def _commit_json(c: Commit | None) -> dict | None:
    return None if c is None else {"sha": c.sha, "subject": c.subject}


def to_json(reports: Sequence[TagReport], skipped: Sequence[str]) -> str:
    payload = {
        "headline": headline(reports),
        "skipped_clones": list(skipped),
        "tags": [
            {
                "service": r.service,
                "tag": r.tag,
                "verdict": r.verdict,
                "problem": r.problem,
                "base": r.base,
                "base_kind": r.base_kind,
                "default": r.default,
                "units": [
                    {
                        "unit": u.name,
                        "keyed": u.keyed,
                        "found": u.method is not None,
                        "method": u.method,
                        "evidence": _commit_json(u.evidence),
                        "commits": [_commit_json(c) for c in u.commits],
                    }
                    for u in r.units
                ],
                "reverted": [_commit_json(c) for c in r.reverted],
                "not_needed": [_commit_json(c) for c in r.not_needed],
            }
            for r in reports
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def render(reports: Sequence[TagReport], skipped: Sequence[str]) -> str:
    head = headline(reports)
    lines = ["# Hotfix merge-back", ""]
    lines.append(
        f"**{head['MISSING']} tag(s) MISSING** ({head['missing_units']} unit(s) not on the"
        f" default branch), {head['BACK-PORTED']} BACK-PORTED, {head['NOT-NEEDED']} NOT-NEEDED,"
        f" {head['ERROR']} ERROR."
    )
    if skipped:
        lines += ["", f"Skipped copied clones: {', '.join(skipped)}."]
    lines += [
        "",
        "| Service | Tag | Base | Verdict | Units | Missing |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports:
        base = f"{r.base_kind} ({r.base[:8]})" if r.base and r.base_kind else "-"
        missing = ", ".join(u.name for u in r.missing) or "-"
        units = "?" if r.problem else str(len(r.units))
        cells = [r.service, r.tag, base, f"**{r.verdict}**", units, missing]
        lines.append("| " + " | ".join(_md(c) for c in cells) + " |")

    for r in reports:
        lines += ["", f"## {r.service} {r.tag}: {r.verdict}", ""]
        if r.problem:
            lines.append(f"**Error:** {r.problem}")
            continue
        for u in r.units:
            commits = ", ".join(c.short for c in u.commits)
            label = u.name if u.keyed else f'"{u.name}"'
            if u.method is None:
                lines.append(f"- **{_md(label)}: not found** ({commits})")
            else:
                evidence = f" in {u.evidence.short} {_md(u.evidence.subject)}" if u.evidence else ""
                lines.append(f"- {_md(label)}: found by {u.method}{evidence} ({commits})")
        if r.not_needed:
            lines.append(f"- not needed (non-product files only): {len(r.not_needed)} commit(s)")
            lines += [f"  - {c.short} {_md(c.subject)}" for c in r.not_needed]
        if r.reverted:
            lines.append(f"- reverted inside the range: {len(r.reverted)} commit(s) dropped")
        if not r.units and not r.not_needed and not r.reverted:
            lines.append("- nothing between the base and the tag")
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="hotfix_mergeback",
        description="Fixes running in production that the default branch does not carry.",
    )
    src = p.add_argument_group("what to check (combinable)")
    src.add_argument("--tag", action="append", default=[], help="REPO:TAG (repeatable)")
    src.add_argument("--production", help="version manifest: check every pinned ref")
    src.add_argument("--release-manifest", help="manifest: check every hotfix version in it")
    src.add_argument("--since", help="hotfix tags created at or after this ISO time")
    src.add_argument("--until", help="hotfix tags created before this ISO time")
    p.add_argument("--workspace", default=".", help="directory holding the service clones")
    p.add_argument(
        "--default-branch",
        "--mainline",
        dest="default_branch",
        help="default branch (default: origin/HEAD, then main, then master)",
    )
    p.add_argument(
        "--hotfix-pattern",
        default=DEFAULT_HOTFIX_PATTERN,
        help="regex for hotfix tags; a named group 'base' names the release tag",
    )
    p.add_argument("--non-product-file", default=str(DEFAULT_NON_PRODUCT_FILE))
    p.add_argument("--key-pattern", default=DEFAULT_KEY_PATTERN, help="regex for ticket keys")
    p.add_argument("--key-prefix", action="append", default=[], help="ticket key prefix")
    p.add_argument("--clone-base", help="clone a missing service from URL/<repo> (blobless)")
    p.add_argument("--no-fetch", action="store_true", help="work from local refs only")
    p.add_argument("--json", action="store_true", help="JSON instead of markdown")
    p.add_argument(
        "--fail-on-missing", action="store_true", help="exit 2 on a MISSING or ERROR verdict"
    )
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    workspace = Path(args.workspace)
    try:
        key_re = key_regex(args.key_prefix, args.key_pattern)
        hotfix_re = re.compile(args.hotfix_pattern)
        globs = load_globs(args.non_product_file)
        targets = [parse_target(v) for v in args.tag]
        if args.production:
            targets += manifest_targets(args.production, None)
        if args.release_manifest:
            targets += manifest_targets(args.release_manifest, hotfix_re)
        skipped: list[str] = []
        if args.since or args.until:
            if not workspace.is_dir():
                raise InputError(f"workspace not found: {workspace}")
            since = parse_time(args.since, "--since") if args.since else None
            until = parse_time(args.until, "--until") if args.until else None
            found, skipped = window_targets(workspace, hotfix_re, since, until)
            targets += found
        if not (args.tag or args.production or args.release_manifest or args.since or args.until):
            raise InputError(
                "name what to check: --tag, --production, --release-manifest or a window"
            )
    except (InputError, re.error, OSError) as exc:
        print(f"hotfix_mergeback: {exc}", file=sys.stderr)
        return 1
    settings = Settings(
        workspace, args.default_branch, hotfix_re, key_re, globs, not args.no_fetch, args.clone_base
    )
    unique = sorted(
        {(t.service, t.tag): t for t in targets}.values(), key=lambda t: (t.service, t.tag)
    )
    reports = [analyse(t, settings) for t in unique]
    text = to_json(reports, skipped) if args.json else render(reports, skipped)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    head = headline(reports)
    if args.fail_on_missing and (head["MISSING"] or head["ERROR"]):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
