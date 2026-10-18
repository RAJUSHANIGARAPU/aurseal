"""
The deterministic half of opening a pull request: pre-flight, title, body.

    python -m tools.slash.pr preflight [--target main]   # exit 1 with the reason
    python -m tools.slash.pr title [BRANCH]
    python -m tools.slash.pr body [--target main] [--reports reports] [--log ...]

The body has five sections: summary, changed files, test health, healing and a
review checklist. Test health with no JUnit XML says the results are missing;
healing with no log, an empty log or nothing since the fork point says "not
measured". Neither ever prints a zero it did not count.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

from tools.slash import healing, junit

PROTECTED = frozenset({"main", "master", "develop", "development", "integration"})
TYPE_ALIASES = {
    "feat": "feat",
    "feature": "feat",
    "fix": "fix",
    "bugfix": "fix",
    "hotfix": "fix",
    "chore": "chore",
    "docs": "docs",
    "doc": "docs",
    "refactor": "refactor",
    "test": "test",
    "tests": "test",
    "ci": "ci",
    "perf": "perf",
    "build": "build",
    "style": "style",
}
MAX_COMMITS = 10
MAX_FILES = 20
SECTIONS = ("## Summary", "## Changed files", "## Test health", "## Healing", "## Review checklist")


def title_from_branch(branch: str) -> str:
    head, sep, rest = branch.partition("/")
    kind = TYPE_ALIASES.get(head.lower()) if sep else None
    words = re.sub(r"[-_/.]+", " ", rest if kind else branch).strip()
    return f"{kind}: {words}" if kind else words


def preflight(branch: str, target: str, gh_ok: bool, ahead: int) -> list[str]:
    problems = []
    if branch in PROTECTED or branch == target:
        problems.append(
            f"'{branch}' is the default or integration branch; open a PR from a feature branch"
        )
    if not gh_ok:
        problems.append("the gh CLI is not authenticated; run `gh auth login`")
    if ahead == 0:
        problems.append(
            f"'{branch}' has no commits ahead of {target}; there is nothing new to propose"
        )
    return problems


@dataclass
class BranchFacts:
    branch: str
    commits: list[str]
    files: list[str]


def build_body(facts: BranchFacts, tests: junit.JUnitSummary, heals: healing.HealingWindow) -> str:
    commits = facts.commits[:MAX_COMMITS]
    files = facts.files[:MAX_FILES]
    out = ["## Summary", ""]
    out += [f"- {c}" for c in commits] or ["- (no commits)"]
    if len(facts.commits) > MAX_COMMITS:
        out.append(f"- ... and {len(facts.commits) - MAX_COMMITS} more")
    out += ["", "## Changed files", ""]
    out += [f"- `{f}`" for f in files] or ["- (none)"]
    if len(facts.files) > MAX_FILES:
        out.append(f"- ... and {len(facts.files) - MAX_FILES} more")
    out += ["", "## Test health", ""]
    if tests.measured:
        out.append(
            f"{tests.total} total, {tests.passed} passed, {tests.failed} failed, "
            f"{tests.skipped} skipped - gate **{tests.gate.upper()}**"
        )
        out += [f"- {c.kind}: `{c.node}`" for c in tests.cases[:10]]
    else:
        out.append(
            "Results missing: no JUnit XML in the reports folder. Run `/test-health-report`."
        )
    out += ["", "## Healing", "", healing.describe(heals).removeprefix("Healing: "), ""]
    out += [
        "## Review checklist",
        "",
        "- [ ] Tests pass locally",
        "- [ ] No regressions in the existing suite",
        "- [ ] Locator files changed by healing are committed",
        "- [ ] New scenario specs are committed",
    ]
    return "\n".join(out) + "\n"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def _base(target: str) -> str:
    for ref in (f"origin/{target}", target):
        if (
            subprocess.run(
                ["git", "rev-parse", "--verify", "-q", ref], capture_output=True
            ).returncode
            == 0
        ):
            return ref
    raise SystemExit(f"cannot find {target} or origin/{target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull request pre-flight, title and body.")
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--target", default="main")
    ttl = sub.add_parser("title")
    ttl.add_argument("branch", nargs="?")
    body = sub.add_parser("body")
    body.add_argument("--target", default="main")
    body.add_argument("--reports", default="reports")
    body.add_argument("--log", default=str(healing.DEFAULT_LOG))
    args = parser.parse_args(argv)

    if args.command == "title":
        print(title_from_branch(args.branch or _git("branch", "--show-current")))
        return 0
    branch = _git("branch", "--show-current")
    base = _base(args.target)
    if args.command == "preflight":
        gh_ok = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
        ahead = int(_git("rev-list", "--count", f"{base}..HEAD") or 0)
        problems = preflight(branch, args.target, gh_ok, ahead)
        for problem in problems:
            print(f"stop: {problem}")
        if not problems:
            print(f"ok: {branch} is {ahead} commit(s) ahead of {base}")
        return 1 if problems else 0
    facts = BranchFacts(
        branch,
        [line for line in _git("log", "--format=%s", f"{base}..HEAD").splitlines() if line],
        [line for line in _git("diff", "--name-only", f"{base}...HEAD").splitlines() if line],
    )
    tests = junit.aggregate(junit.find_reports(args.reports))
    heals = healing.read_window(args.log, healing.fork_point_time(args.target), inclusive=False)
    print(build_body(facts, tests, heals), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
