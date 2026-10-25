"""Propose a verified fix for a failed main-branch run, for the Repair workflow to open as a PR.

``python -m tools.repair --log failed.log --summary repair.md`` re-runs the checks in scope,
and when one still fails asks an OpenRouter model for replacement file contents. A proposal is
kept only when every check in scope passes afterwards; otherwise every file is restored.

Scopes (``REPAIR_SCOPE``, comma-separated, default ``lint``):

* ``lint``: ``ruff check .`` and ``ruff format --check .``;
* ``tests``: the ``FAILED`` node ids in the run log, re-run on their own.

A proposal is refused outright when it touches a file the failure did not name, creates a file,
edits a guarded path (workflows, thresholds, waivers, baselines, pins), adds a skip, xfail,
known-defect marker, ``noqa`` or ``type: ignore``, or removes an ``assert`` from a test. Those are
the ways a failing check is turned green without being fixed.

The outcome is written to ``$GITHUB_OUTPUT`` as ``status``: ``fixed`` (the working tree holds a
verified change), ``clean`` (the checks pass without one), ``unfixable``, ``no_model`` (no key or
model configured) or ``out_of_scope``. The exit status is 0 for every outcome; 2 means bad input.
"""

from __future__ import annotations

import argparse
import fnmatch
import http.client
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_HOST = "openrouter.ai"
API_PATH = "/api/v1/chat/completions"

GUARDED = (
    ".github/*",
    "checks/mutation/mutation.toml",
    "checks/security/*allow*",
    "release-policy.yaml",
    "release-waivers.yaml",
    "leakgate-allow.toml",
    "visual_baselines/*",
    "defects/*",
    "requirements*.txt",
    "pyproject.toml",
    "pytest.ini",
    "conftest.py",
    "*/conftest.py",
)
ESCAPES = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail|known_defect)\b|pytest\.(skip|xfail)\(|--deselect"
    r"|#\s*noqa|#\s*type:\s*ignore|#\s*pragma:\s*no\s*cover"
)
FAILED = re.compile(r"(?:^|\s)FAILED (\S+::\S+)(?=\s+-\s|\s*$)", re.MULTILINE)
PY_PATH = re.compile(r"(?<![\w/.-])((?:/[\w.-]+)*[\w.-]+(?:/[\w.-]+)*\.py)\b")
MAX_FILES = 8
MAX_FILE_BYTES = 60_000
MAX_OUTPUT_CHARS = 12_000

Complete = Callable[[list[dict[str, str]]], str]
"""Sends chat messages to the model, returns the reply text."""
Runner = Callable[[Sequence[str]], tuple[int, str]]


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]


@dataclass
class Result:
    status: str
    notes: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)


def run_command(argv: Sequence[str]) -> tuple[int, str]:
    done = subprocess.run(list(argv), cwd=ROOT, capture_output=True, text=True, check=False)
    return done.returncode, (done.stdout + done.stderr)[-MAX_OUTPUT_CHARS:]


def failed_tests(log: str, root: Path = ROOT) -> list[str]:
    ids = []
    for node in FAILED.findall(log):
        if (root / node.split("::", 1)[0]).is_file() and node not in ids:
            ids.append(node)
    return ids


def checks_for(scopes: Sequence[str], log: str, root: Path = ROOT) -> list[Check]:
    checks = []
    if "lint" in scopes:
        checks += [
            Check("ruff check", ("ruff", "check", ".")),
            Check("ruff format", ("ruff", "format", "--check", ".")),
        ]
    if "tests" in scopes and (ids := failed_tests(log, root)):
        pytest = (sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider")
        checks.append(Check("failed tests", (*pytest, "-q", *ids)))
    return checks


def is_guarded(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in GUARDED)


def named_files(outputs: Sequence[str], root: Path = ROOT) -> list[str]:
    """Repository files a failure names, in order of first mention, guarded paths excluded."""
    found: list[str] = []
    for text in outputs:
        for raw in PY_PATH.findall(text):
            path = Path(raw)
            if path.is_absolute():
                try:
                    path = path.relative_to(root)
                except ValueError:
                    continue
            rel = path.as_posix()
            if rel not in found and not is_guarded(rel) and (root / rel).is_file():
                found.append(rel)
    return found[:MAX_FILES]


def violations(path: str, old: str | None, new: str, allowed: Sequence[str]) -> list[str]:
    if old is None:
        return [f"{path}: creates a file"]
    found = []
    if is_guarded(path):
        found.append(f"{path}: guarded path")
    if path not in allowed:
        found.append(f"{path}: not named by the failure")
    if len(ESCAPES.findall(new)) > len(ESCAPES.findall(old)):
        found.append(f"{path}: adds a skip, xfail, noqa or similar escape")
    if path.startswith("tests/") and new.count("assert") < old.count("assert"):
        found.append(f"{path}: removes an assertion")
    return found


def parse_reply(text: str) -> tuple[str, dict[str, str]]:
    """``{"summary": str, "files": [{"path", "content"}]}``, fenced or bare."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the reply holds no JSON object")
    data = json.loads(text[start : end + 1])
    files = {}
    for item in data.get("files") or []:
        if not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            raise ValueError("every file needs a string path and content")
        files[item["path"]] = item["content"]
    return str(data.get("summary") or ""), files


SYSTEM = (
    "You repair a failing check in a Python repository. Fix the cause in the code. Never "
    "weaken a test, skip or deselect it, add noqa or type: ignore, or change a threshold; "
    "such proposals are rejected automatically. Change only the files shown. Reply with one "
    'JSON object and nothing else: {"summary": "<one sentence>", "files": [{"path": '
    '"<repo-relative path>", "content": "<complete new file content>"}]}.'
)


def _prompt(failures: dict[str, str], files: dict[str, str], feedback: str) -> str:
    parts = [f"## {name} fails\n```\n{output}\n```" for name, output in failures.items()]
    parts += [f"## {path}\n```python\n{text}\n```" for path, text in files.items()]
    if feedback:
        parts.append(f"## Your previous proposal was not accepted\n{feedback}")
    return "\n\n".join(parts)


def _read(root: Path, path: str) -> str | None:
    target = root / path
    return target.read_text(encoding="utf-8") if target.is_file() else None


def _failing(checks: Sequence[Check], runner: Runner) -> dict[str, str]:
    failing = {}
    for check in checks:
        code, output = runner(check.argv)
        if code != 0:
            failing[check.name] = output
    return failing


def repair(
    checks: Sequence[Check],
    complete: Complete | None,
    *,
    runner: Runner = run_command,
    root: Path = ROOT,
    attempts: int = 2,
) -> Result:
    if not checks:
        return Result("out_of_scope", ["nothing in scope failed in this run"])
    failing = _failing(checks, runner)
    if not failing:
        return Result("clean", ["every check in scope passes on this commit"])
    if complete is None:
        return Result("no_model", ["OPENROUTER_API_KEY or REPAIR_MODEL is not set"])
    originals: dict[str, str] = {}
    feedback, notes = "", []
    for attempt in range(1, attempts + 1):
        allowed = named_files(list(failing.values()), root)
        if not allowed:
            notes.append("the failure names no file that may be changed")
            break
        context = {p: _read(root, p) or "" for p in allowed}
        context = {p: t for p, t in context.items() if len(t.encode()) <= MAX_FILE_BYTES}
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(failing, context, feedback)},
        ]
        try:
            summary, proposal = parse_reply(complete(messages))
        except (ValueError, json.JSONDecodeError) as exc:
            feedback = f"The reply could not be read: {exc}."
            notes.append(f"attempt {attempt}: unreadable reply ({exc})")
            continue
        refused = [
            v
            for p, t in proposal.items()
            for v in violations(p, originals.get(p, _read(root, p)), t, list(context))
        ]
        if refused or not proposal:
            feedback = "Refused: " + ("; ".join(refused) or "no files were changed") + "."
            notes.append(f"attempt {attempt}: {feedback}")
            continue
        for path, text in proposal.items():
            originals.setdefault(path, _read(root, path) or "")
            (root / path).write_text(text, encoding="utf-8")
        failing = _failing(checks, runner)
        if not failing:
            notes.append(f"attempt {attempt}: {summary}")
            return Result("fixed", notes, sorted(originals))
        feedback = "The checks still fail; the output above is after your change."
        notes.append(f"attempt {attempt}: still failing ({', '.join(failing)})")
    for path, text in originals.items():
        (root / path).write_text(text, encoding="utf-8")
    return Result("unfixable", notes)


def openrouter(key: str, model: str, timeout: float = 180) -> Complete:
    def complete(messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": model, "messages": messages, "temperature": 0})
        conn = http.client.HTTPSConnection(API_HOST, timeout=timeout)
        try:
            conn.request(
                "POST",
                API_PATH,
                body=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            response = conn.getresponse()
            payload = response.read().decode("utf-8", "replace")
        finally:
            conn.close()
        if response.status != 200:
            raise ValueError(f"OpenRouter answered HTTP {response.status}")
        return json.loads(payload)["choices"][0]["message"]["content"]

    return complete


def render(result: Result, run_id: str, scopes: Sequence[str]) -> str:
    lines = [f"Repair of main run {run_id}: **{result.status}** (scope: {', '.join(scopes)})", ""]
    lines += [f"- {note}" for note in result.notes]
    if result.changed:
        lines += ["", "Changed: " + ", ".join(f"`{p}`" for p in result.changed)]
        lines += ["", "Every check in scope passed after the change; review it before merging."]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.repair")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.log.is_file():
        print(f"no such log: {args.log}", file=sys.stderr)
        return 2
    scopes = [s.strip() for s in os.environ.get("REPAIR_SCOPE", "lint").split(",") if s.strip()]
    key, model = os.environ.get("OPENROUTER_API_KEY", ""), os.environ.get("REPAIR_MODEL", "")
    complete = openrouter(key, model) if key and model else None
    log = args.log.read_text(encoding="utf-8", errors="replace")
    result = repair(checks_for(scopes, log), complete)
    args.summary.write_text(render(result, os.environ.get("RUN_ID", "?"), scopes), "utf-8")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"status={result.status}\n")
    print(result.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
