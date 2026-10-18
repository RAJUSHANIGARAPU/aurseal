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

Two entry points:

* ``python -m tools.push_guard`` (or the file path) as a ``PreToolUse`` hook: reads the hook
  JSON from stdin, exits 2 with a one-line reason on stderr to block, 0 to allow;
* ``python -m tools.push_guard deny-list`` prints the matching ``permissions.deny``
  entries (``origin`` and wildcard remotes, ``HEAD:`` forms, force variants) for
  ``.claude/settings.json``.

The default branch name is read once, from ``PUSH_GUARD_BRANCH`` (default ``main``).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Iterator, Sequence

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


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["deny-list"]:
        print(json.dumps({"permissions": {"deny": deny_list()}}, indent=2))
        return 0
    code, message = hook(sys.stdin.read())
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
