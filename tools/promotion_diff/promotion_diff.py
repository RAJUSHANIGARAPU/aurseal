"""What promoting one environment's versions onto another would change, per service.

Given two version manifests (each maps service -> git ref deployed there) and
local clones of the services, the report lists for every service:

* the commits FROM has that TO lacks: what the promotion ships, with ticket
  keys pulled out by a configurable regex (from the subject, else the body) and
  housekeeping split off as noise. When that range holds only merge commits,
  the merges along the first parent are listed instead ("merge commits used").
  A FROM-only commit whose patch TO already carries under another sha is left
  out of the list and counted;
* the inversion check: commits only TO has (typically hotfixes made straight on
  the target) that the promotion would REGRESS. A TO-only commit is not a
  regression when FROM already carries it as the same patch (patch-id), under
  the same normalised subject, or with identical content in every file it
  touched. When TO's divergence has no net content (``git diff base TO`` is
  empty) nothing regresses. For what does regress, the report names the lowest
  version tag on the default branch that carries an equivalent of every one;
* a downgrade flag when FROM is an ancestor of TO;
* for each ticket key, the test files under ``--test-root`` that mention it.
  A mention is not an assertion: "mentioned" is all that column claims;
* settings drift, when the manifests name a settings file per service.

Manifest format (JSON or YAML)::

    settings: settings/{env}/{service}.env  # optional, relative to the manifest
    services:
      orders: v1.4.2                       # clone at <workspace>/orders
      billing: {ref: 3f2c1e0, repo: ../billing-service, settings: billing.env}
      search: 512-3f2c1e0                  # build number + sha: resolves the sha

A flat ``{service: ref}`` mapping works too. ``{env}`` is the manifest's file stem.

``--json --candidate-id ID`` writes the machine-readable form described by
``traceability/schemas/promotion-diff.schema.json``.

Exit status: 0 report written; 1 bad input; 2 with ``--strict`` when a
promotion would regress a commit, would downgrade a service, or a service
pinned in both manifests could not be compared (an unmeasured service is not a
clean one).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_NOISE_FILE = Path(__file__).with_name("noise_patterns.txt")
DEFAULT_HAZARDS_FILE = Path(__file__).with_name("settings_hazards.json")
DEFAULT_KEY_PATTERN = r"\b[A-Z][A-Z0-9]{1,9}-\d+\b"
# "fix totals (#123)", "fix totals (pull request #45)", "fix totals (PR #6)"
DEFAULT_PR_SUFFIX = r"\s*\((?:pull request |pr )?#\d+\)\s*$"
# a version pin in a settings line: 1.4.2, v2.0.0-rc.1
DEFAULT_SETTINGS_MASKS = (r"\bv?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b",)
WORKSPACE_ENV = "PROMOTION_DIFF_WORKSPACE"
_TEXT_SUFFIXES = (".py", ".java", ".kt", ".ts", ".js", ".feature", ".md", ".yml", ".yaml", ".json")
_BUILD_REF = re.compile(r"-([0-9a-fA-F]{7,40})$")
_VERSION_TAG = re.compile(r"^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?$")
_INLINE_COMMENT = re.compile(r"\s+#.*$")
_DEFAULT_BRANCHES = ("origin/main", "origin/master", "main", "master")


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
    settings: str | None = None  # resolved path of this env's settings file


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
    template = None
    if isinstance(data, dict) and isinstance(data.get("services"), dict):
        template = data.get("settings")
        if template is not None and not isinstance(template, str):
            raise InputError(f"{source}: 'settings' must be a path template")
        data = data["services"]
    if not isinstance(data, dict):
        raise InputError(f"{source}: expected a mapping of service to ref")
    pins = {}
    for service, value in data.items():
        settings = None
        if isinstance(value, dict):
            ref, repo, settings = value.get("ref"), value.get("repo"), value.get("settings")
        else:
            ref, repo = value, None
        if not isinstance(ref, (str, int)) or not str(ref).strip():
            raise InputError(f"{source}: service {service!r} has no ref")
        if settings is None and template:
            try:
                settings = template.format(service=service, env=source.stem)
            except (KeyError, IndexError, ValueError) as exc:
                raise InputError(f"{source}: bad settings template ({exc})") from None
        resolved = str(source.parent / str(settings)) if settings else None
        pins[str(service)] = Pin(str(service), str(ref).strip(), repo, resolved)
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


@dataclass(frozen=True)
class Hazard:
    pattern: re.Pattern[str]
    side: str  # "to": a line only TO has; "either": a line only one side has
    message: str


def load_hazards(path: str | Path) -> list[Hazard]:
    """``[{pattern, side, message}]`` from a JSON file; an empty list is allowed."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"hazard file {path}: {exc}") from None
    if isinstance(data, dict):
        data = data.get("hazards")
    if not isinstance(data, list):
        raise InputError(f"hazard file {path}: expected a list of hazards")
    hazards = []
    for number, entry in enumerate(data, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
            raise InputError(f"hazard file {path}: entry {number} has no pattern")
        side = entry.get("side", "either")
        if side not in ("to", "either"):
            raise InputError(f"hazard file {path}: entry {number} side must be to or either")
        try:
            pattern = re.compile(entry["pattern"])
        except re.error as exc:
            raise InputError(f"hazard file {path}: entry {number} bad pattern ({exc})") from None
        hazards.append(Hazard(pattern, side, str(entry.get("message") or entry["pattern"])))
    return hazards


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str = ""

    @property
    def short(self) -> str:
        return self.sha[:8]


class Git:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(
        self, *args: str, check: bool = True, stdin: str | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=check,
            input=stdin,
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

    def _verify(self, rev: str) -> str | None:
        done = self.run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
        return done.stdout.strip() if done.returncode == 0 else None

    def resolve(self, ref: str) -> str | None:
        """A ref, tag or branch; else a build-number ref ``<build>-<sha>`` by its sha."""
        for candidate in (ref, f"refs/tags/{ref}", f"origin/{ref}"):
            sha = self._verify(candidate)
            if sha:
                return sha
        match = _BUILD_REF.search(ref)
        return self._verify(match.group(1)) if match else None

    def commits(self, rev_range: str, *, first_parent: bool = False) -> list[Commit]:
        """Newest first. Merges are left out, unless walking the first parent."""
        walk = "--first-parent" if first_parent else "--no-merges"
        raw = self.out("log", walk, "--format=%H%x1f%s%x1f%b%x1e", rev_range)
        out = []
        for record in raw.split("\x1e"):
            parts = record.strip("\n").split("\x1f", 2)
            if len(parts) == 3:
                out.append(Commit(parts[0], parts[1], parts[2].strip()))
        return out

    def count(self, rev_range: str) -> int:
        return int(self.out("rev-list", "--count", rev_range).strip() or 0)

    def merge_base(self, a: str, b: str) -> str | None:
        done = self.run("merge-base", a, b, check=False)
        return done.stdout.strip() if done.returncode == 0 else None

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.run("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def same_content(self, a: str, b: str) -> bool:
        return self.run("diff", "--quiet", a, b, check=False).returncode == 0

    def patch_ids(self, rev_range: str) -> dict[str, str]:
        """``sha -> stable patch-id`` for the non-merge commits of a range, newest first."""
        patches = self.out("log", "-p", "--no-merges", "--no-color", "--no-ext-diff", rev_range)
        if not patches.strip():
            return {}
        ids = {}
        for line in self.run("patch-id", "--stable", stdin=patches).stdout.splitlines():
            patch_id, _, sha = line.partition(" ")
            if sha:
                ids[sha.strip()] = patch_id
        return ids

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

    def version_tags_containing(self, sha: str) -> list[str]:
        return sorted((t for t in self.tags_containing(sha) if _VERSION_TAG.match(t)), key=semver)

    def default_branch(self, override: str | None = None) -> str | None:
        """The origin default branch (``origin/HEAD``), else main/master, as a sha."""
        if override:
            return self._verify(override)
        head = self.run("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
        candidates = [head.stdout.strip()] if head.returncode == 0 else []
        for candidate in [*candidates, *_DEFAULT_BRANCHES]:
            sha = self._verify(candidate)
            if sha:
                return sha
        return None


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def semver(tag: str) -> tuple:
    """Sort key: numeric parts, then a suffixed tag (``-hotfix-2``) after its base."""
    match = _VERSION_TAG.match(tag)
    if not match:
        return ((), 2, ((1, tag),))
    numbers = tuple(int(n) for n in match.group(1).split("."))
    suffix = match.group(2)
    parts = tuple(
        (0, int(p)) if p.isdigit() else (1, p) for p in re.findall(r"\d+|\D+", suffix or "")
    )
    return (numbers, 0 if suffix is None else 1, parts)


def normalise_subject(subject: str, pr_suffix: re.Pattern[str] | None = None) -> str:
    """Lowercase, whitespace collapsed, a trailing pull-request reference dropped."""
    text = pr_suffix.sub("", subject) if pr_suffix else subject
    return " ".join(text.lower().split())


@dataclass
class Inverted:
    commit: Commit
    equivalence: str | None  # None => the promotion would regress it
    tags: list[str] = field(default_factory=list)
    equivalent: str | None = None  # its counterpart on the default branch
    fixed_tags: list[str] = field(default_factory=list)


@dataclass
class SettingsDrift:
    service: str
    from_file: str | None
    to_file: str | None
    to_only: list[str] = field(default_factory=list)
    from_only: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    problem: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "fromFile": self.from_file,
            "toFile": self.to_file,
            "toOnly": self.to_only,
            "fromOnly": self.from_only,
            "warnings": self.warnings,
            "notCompared": self.problem,
        }


@dataclass
class ServiceDelta:
    service: str
    from_ref: str | None
    to_ref: str | None
    problem: str | None = None
    commits: list[Commit] = field(default_factory=list)
    inverted: list[Inverted] = field(default_factory=list)
    already_in_target: list[Commit] = field(default_factory=list)
    merge_commits_used: bool = False
    net_zero: bool = False
    downgrade: bool = False
    fixed_in: str | None = None
    fixed_in_checked: bool = False
    settings: SettingsDrift | None = None

    @property
    def target_only(self) -> list[Inverted]:
        return [i for i in self.inverted if i.equivalence is None]

    @property
    def regressions(self) -> list[Inverted]:
        """TO-only commits the promotion removes; none when TO's divergence nets to zero."""
        return [] if self.net_zero else self.target_only

    def notes(self) -> list[str]:
        notes = []
        if self.downgrade:
            notes.append("downgrade")
        if self.merge_commits_used:
            notes.append("merge commits used")
        if self.already_in_target:
            notes.append(f"{len(self.already_in_target)} already in target")
        if self.net_zero and self.target_only:
            notes.append("net zero")
        if self.settings and (self.settings.to_only or self.settings.from_only):
            notes.append("settings drift")
        return notes


def keys_in(text: str, key_re: re.Pattern[str]) -> list[str]:
    """Keys in order of first appearance, without repeats."""
    return list(dict.fromkeys(key_re.findall(text)))


def commit_keys(
    commit: Commit, key_re: re.Pattern[str], ignore: frozenset[str] = frozenset()
) -> list[str]:
    """Keys from the subject; from the body only when the subject has none."""
    keys = [k for k in keys_in(commit.subject, key_re) if k not in ignore]
    if not keys:
        keys = [k for k in keys_in(commit.body, key_re) if k not in ignore]
    return keys


def is_noise(subject: str, noise: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(subject) for p in noise)


def split_commits(
    commits: Sequence[Commit],
    key_re: re.Pattern[str],
    noise: Sequence[re.Pattern[str]],
    ignore: frozenset[str] = frozenset(),
) -> tuple[dict[str, list[Commit]], list[Commit], list[Commit]]:
    """A commit naming a key is a change for that key; otherwise noise or unkeyed.

    Walks oldest first, so keys come out in order of first appearance.
    """
    keyed: dict[str, list[Commit]] = {}
    unkeyed, noisy = [], []
    for commit in reversed(commits):
        keys = commit_keys(commit, key_re, ignore)
        if keys:
            for key in keys:
                keyed.setdefault(key, []).append(commit)
        elif is_noise(commit.subject, noise):
            noisy.append(commit)
        else:
            unkeyed.append(commit)
    return keyed, unkeyed, noisy


def check_inversion(
    git: Git, from_sha: str, to_sha: str, pr_suffix: re.Pattern[str] | None = None
) -> list[Inverted]:
    """Commits only TO has, each with the equivalence that clears it (or None)."""
    if git.is_ancestor(to_sha, from_sha):
        return []
    from_subjects = {
        normalise_subject(c.subject, pr_suffix) for c in git.commits(f"{to_sha}..{from_sha}")
    }
    subjects = {c.sha: c for c in git.commits(f"{from_sha}..{to_sha}")}
    result = []
    for patch_equal, sha in git.cherry(from_sha, to_sha):
        commit = subjects.get(sha)
        if commit is None:  # a merge commit: log --no-merges left it out
            continue
        if patch_equal:
            reason = "same patch"
        elif normalise_subject(commit.subject, pr_suffix) in from_subjects:
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


def find_fixed_in(
    git: Git,
    regressions: Sequence[Inverted],
    from_sha: str,
    to_sha: str,
    base: str,
    default_sha: str,
    pr_suffix: re.Pattern[str] | None = None,
) -> str | None:
    """The lowest version tag carrying an equivalent of every regressing commit.

    The equivalent on the default branch has the same patch-id, else it is the oldest
    commit in ``base..default`` with the same normalised subject. Sets ``equivalent``
    and ``fixed_tags`` on each regression as it goes.
    """
    default_range = f"{base}..{default_sha}"
    by_patch: dict[str, str] = {}
    for sha, patch_id in reversed(list(git.patch_ids(default_range).items())):
        by_patch.setdefault(patch_id, sha)  # oldest first
    by_subject: dict[str, str] = {}
    for commit in reversed(git.commits(default_range)):
        by_subject.setdefault(normalise_subject(commit.subject, pr_suffix), commit.sha)
    own_patch = git.patch_ids(f"{from_sha}..{to_sha}")
    common: set[str] | None = None
    for inv in regressions:
        equivalent = by_patch.get(own_patch.get(inv.commit.sha, "")) or by_subject.get(
            normalise_subject(inv.commit.subject, pr_suffix)
        )
        inv.equivalent = equivalent
        inv.fixed_tags = git.version_tags_containing(equivalent) if equivalent else []
        tags = set(inv.fixed_tags)
        common = tags if common is None else common & tags
    return min(common, key=semver) if common else None


def analyse_service(
    workspace: Path,
    service: str,
    from_pin: Pin | None,
    to_pin: Pin | None,
    fetch: bool,
    pr_suffix: re.Pattern[str] | None = None,
    default_branch: str | None = None,
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
    rev_range = f"{to_sha}..{from_sha}"
    delta.downgrade = from_sha != to_sha and git.is_ancestor(from_sha, to_sha)
    commits = git.commits(rev_range)
    if not commits and git.count(rev_range) > 0:
        commits = git.commits(rev_range, first_parent=True)
        delta.merge_commits_used = True
    base = git.merge_base(from_sha, to_sha)
    if base and commits:
        target_patches = set(git.patch_ids(f"{base}..{to_sha}").values())
        source_patches = git.patch_ids(f"{base}..{from_sha}")
        for commit in commits:
            if source_patches.get(commit.sha) in target_patches:
                delta.already_in_target.append(commit)
            else:
                delta.commits.append(commit)
    else:
        delta.commits = commits
    delta.inverted = check_inversion(git, from_sha, to_sha, pr_suffix)
    if base and delta.target_only and git.same_content(base, to_sha):
        delta.net_zero = True
    if base and delta.regressions:
        default_sha = git.default_branch(default_branch)
        if default_sha:
            delta.fixed_in_checked = True
            delta.fixed_in = find_fixed_in(
                git, delta.regressions, from_sha, to_sha, base, default_sha, pr_suffix
            )
    return delta


def normalise_settings(text: str, masks: Sequence[re.Pattern[str]]) -> list[str]:
    """Comments and blank lines dropped, whitespace collapsed, version pins masked."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//", ";")):
            continue
        line = " ".join(_INLINE_COMMENT.sub("", line).split())
        for mask in masks:
            line = mask.sub("<pin>", line)
        if line:
            lines.append(line)
    return lines


def settings_drift(
    service: str,
    from_pin: Pin | None,
    to_pin: Pin | None,
    masks: Sequence[re.Pattern[str]],
    hazards: Sequence[Hazard],
) -> SettingsDrift | None:
    """Multiset diff of the two sides' settings; None when neither side names a file."""
    from_file = from_pin and from_pin.settings
    to_file = to_pin and to_pin.settings
    if not from_file and not to_file:
        return None
    drift = SettingsDrift(service, from_file, to_file)
    texts = []
    for label, path in (("FROM", from_file), ("TO", to_file)):
        if not path:
            drift.problem = f"no settings file named for {label}"
            return drift
        try:
            texts.append(Path(path).read_text(encoding="utf-8"))
        except OSError:
            drift.problem = f"{label} settings file not found: {path}"
            return drift
    source = Counter(normalise_settings(texts[0], masks))
    target = Counter(normalise_settings(texts[1], masks))
    drift.to_only = sorted((target - source).elements())
    drift.from_only = sorted((source - target).elements())
    sides = [("to", line) for line in drift.to_only] + [("from", line) for line in drift.from_only]
    for hazard in hazards:
        for side, line in sides:
            if (hazard.side == "either" or side == "to") and hazard.pattern.search(line):
                drift.warnings.append({"line": line, "side": side, "message": hazard.message})
    return drift


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


@dataclass(frozen=True)
class Options:
    key_re: re.Pattern[str]
    noise: Sequence[re.Pattern[str]]
    ignore: frozenset[str] = frozenset()
    coverage: dict[str, list[str]] | None = None


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _service_lines(d: ServiceDelta, labels: tuple[str, str], opts: Options) -> list[str]:
    source, target = labels
    lines: list[str] = []
    if d.problem:
        return [f"**Not compared:** {d.problem}"]
    if d.downgrade:
        lines.append(
            f"**Target is ahead: promotion is a downgrade** ({target} already has {source})."
        )
    if not d.commits and not d.inverted and not d.already_in_target:
        return lines + ["No change: both environments run the same code."]
    if d.merge_commits_used:
        lines.append(
            "Merge commits used: the delta holds only merges, listed along the first parent."
        )
    keyed, unkeyed, noisy = split_commits(d.commits, opts.key_re, opts.noise, opts.ignore)
    if keyed:
        lines.append(f"Ships {len(keyed)} key(s):")
        for key, commits in keyed.items():
            subjects = "; ".join(f"{c.subject} ({c.short})" for c in commits)
            tests = ""
            if opts.coverage is not None:
                found = opts.coverage.get(key)
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
    if d.already_in_target:
        lines.append(f"{len(d.already_in_target)} already in target under another SHA.")
    regressions = d.regressions
    if regressions:
        lines.append(f"**Would regress {len(regressions)} commit(s) only {target} has:**")
        for inv in regressions:
            tags = f" (in tags: {', '.join(inv.tags)})" if inv.tags else ""
            lines.append(f"- {_md(inv.commit.subject)} ({inv.commit.short}){tags}")
        if not d.fixed_in_checked:
            lines.append("Fixed-in not checked: no default branch found.")
        elif d.fixed_in:
            lines.append(f"Fixed in {d.fixed_in}: it carries an equivalent of every one.")
        else:
            lines.append("No released tag carries all of them.")
    if d.net_zero and d.target_only:
        lines.append(f"{target}-only commits with no net content (net zero, nothing regresses):")
        lines += [f"- {_md(i.commit.subject)} ({i.commit.short})" for i in d.target_only]
    cleared = [i for i in d.inverted if i.equivalence]
    if cleared:
        lines.append(f"{target}-only commits already carried by {source}:")
        lines += [f"- {_md(i.commit.subject)} ({i.commit.short}): {i.equivalence}" for i in cleared]
    return lines


def _settings_lines(drift: SettingsDrift) -> list[str]:
    if drift.problem:
        return [f"Settings not compared: {drift.problem}"]
    if not drift.to_only and not drift.from_only:
        return ["Settings: no drift."]
    counts = f"{len(drift.to_only)} target-only, {len(drift.from_only)} source-only"
    lines = [f"Settings drift ({counts}):"]
    lines += [f"- target only: `{_md(line)}`" for line in drift.to_only]
    lines += [f"- source only: `{_md(line)}`" for line in drift.from_only]
    lines += [f"- **warning:** {_md(w['message'])} (`{_md(w['line'])}`)" for w in drift.warnings]
    return lines


def render(
    deltas: Sequence[ServiceDelta],
    labels: tuple[str, str],
    shown: Sequence[tuple[str, dict[str, Pin]]],
    opts: Options,
) -> str:
    source, target = labels
    lines = [f"# Promotion diff: {source} -> {target}", ""]
    header = [
        "Service",
        source,
        target,
        *(name for name, _ in shown),
        "Commits",
        "Keys",
        "Regress",
        "Notes",
    ]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for d in deltas:
        keyed, _, _ = split_commits(d.commits, opts.key_re, opts.noise, opts.ignore)
        row = [
            d.service,
            d.from_ref or "-",
            d.to_ref or "-",
            *((pins[d.service].ref if d.service in pins else "-") for _, pins in shown),
            "?" if d.problem else str(len(d.commits)),
            "?" if d.problem else str(len(keyed)),
            "?" if d.problem else str(len(d.regressions)),
            ", ".join(d.notes()) or "-",
        ]
        lines.append("| " + " | ".join(_md(cell) for cell in row) + " |")
    for d in deltas:
        lines += ["", f"## {d.service}", ""]
        lines += _service_lines(d, labels, opts)
        if d.settings:
            lines += _settings_lines(d.settings)
    return "\n".join(lines) + "\n"


def _commit_json(commit: Commit) -> dict[str, str]:
    return {"sha": commit.sha, "subject": commit.subject}


def to_json(
    deltas: Sequence[ServiceDelta],
    labels: tuple[str, str],
    pins: tuple[dict[str, Pin], dict[str, Pin]],
    opts: Options,
    candidate_id: str,
    generated: str | None = None,
) -> dict[str, Any]:
    """The ``promotion-diff.schema.json`` document."""
    services = []
    for d in deltas:
        keyed, unkeyed, noisy = split_commits(d.commits, opts.key_re, opts.noise, opts.ignore)
        services.append(
            {
                "service": d.service,
                "fromRef": d.from_ref,
                "toRef": d.to_ref,
                "notCompared": d.problem,
                "functional": [
                    {
                        "key": key,
                        "commits": [_commit_json(c) for c in commits],
                        "tests": None if opts.coverage is None else opts.coverage.get(key, []),
                    }
                    for key, commits in keyed.items()
                ],
                "unkeyed": [_commit_json(c) for c in unkeyed],
                "noise": len(noisy),
                "regressing": [
                    {
                        **_commit_json(i.commit),
                        "tags": i.tags,
                        "equivalent": i.equivalent,
                        "fixedTags": i.fixed_tags,
                    }
                    for i in d.regressions
                ],
                "fixedIn": d.fixed_in,
                "fixedInChecked": d.fixed_in_checked,
                "targetOnly": [
                    {**_commit_json(i.commit), "equivalence": i.equivalence} for i in d.inverted
                ],
                "alreadyInTarget": len(d.already_in_target),
                "mergeCommitsUsed": d.merge_commits_used,
                "netZero": d.net_zero,
                "downgrade": d.downgrade,
            }
        )
    return {
        "schemaVersion": 1,
        "candidateId": candidate_id,
        "from": labels[0],
        "to": labels[1],
        "generated": generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pins": {
            side: {name: pin.ref for name, pin in sorted(manifest.items())}
            for side, manifest in zip(("from", "to"), pins, strict=True)
        },
        "services": services,
        "settingsDrift": [d.settings.to_json() for d in deltas if d.settings],
    }


def collect_keys(
    deltas: Sequence[ServiceDelta], key_re: re.Pattern[str], ignore: frozenset[str] = frozenset()
) -> list[str]:
    """Shipped keys in order of first appearance (service by service, oldest commit first)."""
    keys: dict[str, None] = {}
    for d in deltas:
        for commit in reversed(d.commits):
            for key in commit_keys(commit, key_re, ignore):
                keys.setdefault(key, None)
    return list(keys)


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
    p.add_argument(
        "--ignore-key",
        action="append",
        default=[],
        help="placeholder key to ignore (repeatable, or comma-separated)",
    )
    p.add_argument(
        "--pr-suffix-pattern",
        default=DEFAULT_PR_SUFFIX,
        help="regex for a trailing pull-request reference, stripped before comparing subjects",
    )
    p.add_argument(
        "--default-branch", help="branch searched for fixed-in tags (default origin/HEAD)"
    )
    p.add_argument("--test-root", action="append", default=[], help="where tests live (repeatable)")
    p.add_argument(
        "--settings-mask",
        action="append",
        help="regex masked out of settings lines (repeatable; default: version pins)",
    )
    p.add_argument("--settings-hazards", default=str(DEFAULT_HAZARDS_FILE))
    p.add_argument(
        "--no-fetch", action="store_true", help="skip git fetch and work from local refs"
    )
    p.add_argument("--keys", action="store_true", help="list the shipped keys and nothing else")
    p.add_argument("--json", action="store_true", help="write JSON (needs --candidate-id)")
    p.add_argument("--candidate-id", help="release candidate id recorded in the JSON")
    p.add_argument(
        "--strict", action="store_true", help="exit 2 on a regression, a downgrade or a gap"
    )
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.json and not args.candidate_id:
            raise InputError("--json needs --candidate-id")
        key_re = re.compile(args.key_pattern)
        pr_suffix = re.compile(args.pr_suffix_pattern, re.IGNORECASE)
        masks = [re.compile(m) for m in (args.settings_mask or DEFAULT_SETTINGS_MASKS)]
        source, target = load_pins(args.source), load_pins(args.target)
        shown = [(Path(s).stem, load_pins(s)) for s in args.show]
        noise = load_noise(args.noise_file)
        hazards = load_hazards(args.settings_hazards)
        coverage = coverage_index(args.test_root, key_re) if args.test_root else None
    except (InputError, re.error, OSError) as exc:
        print(f"promotion_diff: {exc}", file=sys.stderr)
        return 1
    ignore = frozenset(k.strip() for raw in args.ignore_key for k in raw.split(",") if k.strip())
    opts = Options(key_re, noise, ignore, coverage)
    workspace = Path(args.workspace)
    deltas = []
    for name in sorted(set(source) | set(target)):
        delta = analyse_service(
            workspace,
            name,
            source.get(name),
            target.get(name),
            not args.no_fetch,
            pr_suffix,
            args.default_branch,
        )
        delta.settings = settings_drift(name, source.get(name), target.get(name), masks, hazards)
        deltas.append(delta)
    labels = (Path(args.source).stem, Path(args.target).stem)
    if args.keys:
        text = "\n".join(collect_keys(deltas, key_re, ignore)) + "\n"
    elif args.json:
        doc = to_json(deltas, labels, (source, target), opts, args.candidate_id)
        text = json.dumps(doc, indent=2) + "\n"
    else:
        text = render(deltas, labels, shown, opts)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    for d in deltas:
        for warning in d.settings.warnings if d.settings else ():
            print(f"warning: {d.service} settings: {warning['message']}", file=sys.stderr)
    gap = any(d.problem and d.from_ref and d.to_ref for d in deltas)
    failing = gap or any(d.regressions or d.downgrade for d in deltas)
    if args.strict and failing:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
