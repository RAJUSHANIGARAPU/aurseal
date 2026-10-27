"""Protected-branch push guard for coding-assistant sessions.

Changes reach the default branch through a pull request, never through a push from a local
session. A permission deny list matches command strings, so an unusual spelling slips
past it; this guard parses the command instead and blocks any ``git push`` whose
destination resolves to the default branch:

* explicit refspecs - ``main``, ``+main``, ``HEAD:main``, ``:main`` (a delete),
  ``refs/heads/main``, on any remote, with or without force flags in any position;
* implicit ones - a bare ``git push`` or ``git push <remote>`` while the current branch is
  the default branch, ``HEAD`` as the destination, ``--all`` and ``--mirror``;
* inside compound commands (``cd x && git push origin main``, ``;``, ``|``), but not inside
  a quoted string (``echo "git push origin main"`` is not a push).

Entry points:

* ``python -m tools.push_guard`` (or the file path) as a ``PreToolUse`` hook: reads the hook
  JSON from stdin, exits 2 with a one-line reason on stderr to block, 0 to allow;
* ``python -m tools.push_guard deny-list`` prints the matching ``permissions.deny``
  entries (``origin`` and wildcard remotes, ``HEAD:`` forms, force variants) for
  ``.claude/settings.json``;
* ``python -m tools.push_guard pre-push`` as a git ``pre-push`` hook, for every
  contributor rather than one assistant: blocks when a pushed remote ref is the default
  branch. As a plain git hook it reads the ``<local ref> <sha> <remote ref> <sha>`` lines
  git writes to stdin (deletes included); under pre-commit, which keeps stdin to itself, it
  reads ``PRE_COMMIT_REMOTE_BRANCH``. Exit 1 blocks. ``git push --no-verify`` skips any
  pre-push hook, so the server-side rule (a ruleset requiring a pull request) stays the
  real control;
* ``python -m tools.push_guard merged-pr --repo owner/name --sha <sha>``: the detective
  check CI runs on every default-branch push. Exit 0 when the commit belongs to a merged
  pull request, 1 when it does not (a direct push), 2 when the API could not answer.

The default branch name is read once, from ``PUSH_GUARD_BRANCH`` (default ``main``).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence

BRANCH_ENV = "PUSH_GUARD_BRANCH"
DEFAULT_BRANCH = "main"
BLOCK_EXIT = 2
_OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}
_GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_PUSH_OPTIONS_WITH_VALUE = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}
FORCE_FLAGS = ("-f", "--force", "--force-with-lease")


def default_branch() -> str:
    return (os.environ.get(BRANCH_ENV) or "").strip() or DEFAULT_BRANCH


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return command.split()


def simple_commands(command: str) -> Iterator[list[str]]:
    """Each simple command of a shell line, split at ``&&``, ``;``, ``|`` and friends."""
    current: list[str] = []
    for token in _tokens(command):
        if token in _OPERATORS:
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def _push_args(words: list[str]) -> list[str] | None:
    """The arguments after ``git ... push``, or None when this is not a push."""
    index = 0
    while index < len(words) and "=" in words[index] and not words[index].startswith("-"):
        index += 1  # leading VAR=value assignments
    if index < len(words) and words[index] in ("sudo", "command", "exec"):
        index += 1
    if index >= len(words) or os.path.basename(words[index]) != "git":
        return None
    index += 1
    while index < len(words) and words[index].startswith("-"):
        option = words[index]
        index += 2 if option in _GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(words) or words[index] != "push":
        return None
    return words[index + 1 :]


def _destination(refspec: str) -> str:
    spec = refspec.lstrip("+")
    destination = spec.split(":", 1)[1] if ":" in spec else spec
    return destination.removeprefix("refs/heads/")


def blocked(command: str, current_branch: str | None, branch: str | None = None) -> str | None:
    """The reason ``command`` would push to the default branch, or None."""
    target = branch or default_branch()
    for words in simple_commands(command):
        args = _push_args(words)
        if args is None:
            continue
        positional: list[str] = []
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in ("--all", "--mirror"):
                return f"git push {arg} includes {target}; open a pull request instead"
            if arg in _PUSH_OPTIONS_WITH_VALUE:
                index += 2
                continue
            if not arg.startswith("-"):
                positional.append(arg)
            index += 1
        refspecs = positional[1:]
        if not refspecs:
            if current_branch == target:
                return f"git push from {target} pushes {target}; open a pull request instead"
            continue
        for refspec in refspecs:
            destination = _destination(refspec)
            if destination == "HEAD" and current_branch == target:
                return f"git push {refspec} targets {target} (current branch)"
            if destination == target:
                return f"git push {refspec} targets {target}; open a pull request instead"
    return None


def deny_list(branch: str | None = None) -> list[str]:
    """``permissions.deny`` entries for pushes to the default branch."""
    target = branch or default_branch()
    entries: list[str] = []
    for remote in ("origin", "*"):
        for ref in (target, f"HEAD:{target}", f"HEAD:refs/heads/{target}"):
            entries += [f"Bash(git push {remote} {ref})", f"Bash(git push {remote} {ref} *)"]
            for flag in FORCE_FLAGS:
                entries += [
                    f"Bash(git push {flag} {remote} {ref})",
                    f"Bash(git push {flag} {remote} {ref} *)",
                    f"Bash(git push {remote} {ref} {flag})",
                    f"Bash(git push {remote} {ref} {flag} *)",
                ]
    return entries


def current_branch(cwd: str | None = None) -> str | None:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    name = done.stdout.strip()
    return name if done.returncode == 0 and name and name != "HEAD" else None


def hook(payload: str) -> tuple[int, str]:
    """Exit status and message for one ``PreToolUse`` payload."""
    try:
        data = json.loads(payload or "{}")
    except ValueError:
        return 0, ""
    if data.get("tool_name") not in (None, "Bash"):
        return 0, ""
    command = str((data.get("tool_input") or {}).get("command") or "")
    reason = blocked(command, current_branch(data.get("cwd")))
    if reason:
        return BLOCK_EXIT, f"push guard: {reason}"
    return 0, ""


PRE_PUSH_BLOCK_EXIT = 1
REMOTE_BRANCH_ENV = "PRE_COMMIT_REMOTE_BRANCH"


def pushed_remote_refs(stdin_text: str, environ: Mapping[str, str]) -> list[str]:
    """Remote refs of a push: git's stdin lines, else pre-commit's single remote branch."""
    refs = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            refs.append(parts[2])
    if not refs and (environ.get(REMOTE_BRANCH_ENV) or "").strip():
        refs.append(environ[REMOTE_BRANCH_ENV].strip())
    return refs


def pre_push(
    stdin_text: str, environ: Mapping[str, str] | None = None, branch: str | None = None
) -> tuple[int, str]:
    """Exit status and message for a git ``pre-push`` hook."""
    environ = os.environ if environ is None else environ
    target = branch or default_branch()
    for ref in pushed_remote_refs(stdin_text, environ):
        if ref.removeprefix("refs/heads/") == target:
            return PRE_PUSH_BLOCK_EXIT, (
                f"push guard: this push updates {target} ({ref}); open a pull request instead"
            )
    return 0, ""


Fetch = Callable[[str, Mapping[str, str]], object]


def _fetch_json(url: str, headers: Mapping[str, str]) -> object:
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed API host
        return json.loads(response.read().decode("utf-8"))


def merged_pr(
    repo: str,
    sha: str,
    environ: Mapping[str, str] | None = None,
    fetch: Fetch = _fetch_json,
    attempts: int = 3,
    pause_s: float = 10.0,
    sleep: Callable[[float], None] | None = None,
) -> tuple[int, str]:
    """0 when ``sha`` belongs to a merged pull request, 1 when not, 2 when unknown.

    The association of a just-merged commit can lag the push event by seconds, so "no
    merged pull request" is asked again ``attempts`` times before it is the answer.
    """
    import time

    sleep = sleep or time.sleep
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        code, message = _merged_pr_once(repo, sha, environ, fetch)
        if code != 1 or attempt == attempts:
            return code, message
        sleep(pause_s)
    return code, message  # pragma: no cover - the loop always returns


def _merged_pr_once(
    repo: str, sha: str, environ: Mapping[str, str] | None, fetch: Fetch
) -> tuple[int, str]:
    environ = os.environ if environ is None else environ
    api = (environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    headers = {"Accept": "application/vnd.github+json"}
    token = (environ.get("GITHUB_TOKEN") or environ.get("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        pulls = fetch(f"{api}/repos/{repo}/commits/{sha}/pulls", headers)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 2, f"merged-pr: could not ask the API about {sha[:12]}: {exc}"
    if not isinstance(pulls, list):
        return 2, f"merged-pr: unexpected API answer for {sha[:12]}"
    merged = [p for p in pulls if isinstance(p, dict) and p.get("merged_at")]
    if merged:
        return 0, f"merged-pr: {sha[:12]} came in through pull request #{merged[0].get('number')}"
    return 1, (
        f"merged-pr: {sha[:12]} reached the default branch without a merged pull request "
        "(a direct push). Require pull requests with a ruleset on the default branch."
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["deny-list"]:
        print(json.dumps({"permissions": {"deny": deny_list()}}, indent=2))
        return 0
    if args[:1] == ["pre-push"]:
        stdin_text = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        code, message = pre_push(stdin_text)
        if message:
            print(message, file=sys.stderr)
        return code
    if args[:1] == ["merged-pr"]:
        import argparse

        parser = argparse.ArgumentParser(prog="python -m tools.push_guard merged-pr")
        parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
        parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
        options = parser.parse_args(args[1:])
        code, message = merged_pr(options.repo, options.sha)
        stream = sys.stdout if code == 0 else sys.stderr
        prefix = "" if code == 0 else "::error title=Direct push::" if code == 1 else "::error::"
        print(prefix + message, file=stream)
        return code
    code, message = hook(sys.stdin.read())
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
