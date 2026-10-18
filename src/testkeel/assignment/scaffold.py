"""``new-assignment``: validate, render into a staging dir, lock, leak-gate, self-check, move.

Nothing reaches the output directory before the staged tree is locked, passes the leak
gate and passes its self-check. On a leak-gate or self-check failure the staged tree is
kept (its path is printed) so the finding can be inspected; on any other failure the
staging dir is removed.

Exit codes: 0 created, 1 unexpected error (traceback written to a log file), 2 usage
error, 3 output dir not empty without ``--force``, 4 leak gate failed, 5 lock or
self-check failed, 6 missing prerequisite, 130 interrupted.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testkeel import leakgate
from testkeel.assignment.slug import slugify
from testkeel.assignment.types import AVAILABLE, BY_NAME
from testkeel.leakgate.project import ProjectRules
from testkeel.leakgate.scan import Finding

OK, UNEXPECTED, USAGE, NOT_EMPTY, LEAK, SELF_CHECK, PREREQUISITE, INTERRUPTED = (
    0, 1, 2, 3, 4, 5, 6, 130,
)  # fmt: skip
ANSWERS_FILE = ".copier-answers.yml"
_URL_RE = re.compile(r"^https?://[^\s/]+[^\s]*$")
#: The only index a generated lock file may name; user-level index settings are ignored.
PYPI_SIMPLE = "https://pypi.org/simple"
# Environment variables that would point uv at another index.
_INDEX_VARS = (
    "UV_INDEX", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS",
    "UV_CONFIG_FILE",
)  # fmt: skip
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache"}
_OUTPUT_LINES = 20


class Abort(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StepFailed(Exception):
    """A pipeline step (lock, compile, lint) failed on the staged tree: exit 5."""

    def __init__(self, step: str, detail: list[str]) -> None:
        super().__init__(step)
        self.step = step
        self.detail = detail


@dataclass
class Options:
    type: str
    name: str
    company: str = ""
    task_file: Path | None = None
    target_url: str | None = None
    api_base_url: str | None = None
    python_version: str = "3.12"
    report: str = "html"
    docker: bool = True
    ci: str = "github"
    git: bool = True
    credit: bool = False
    lock: bool = True
    output_dir: Path | None = None
    dry_run: bool = False
    force: bool = False


@dataclass
class Result:
    code: int
    lines: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    staging: Path | None = None
    output: Path | None = None


def cache_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "testkeel"


def task_summary(text: str) -> str:
    """The first heading (or first non-empty line) of a task brief, at most 120 chars."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = next((line for line in lines if line.startswith("#")), lines[0] if lines else "")
    return heading.lstrip("#").strip()[:120]


def _validate(opts: Options) -> tuple[str, dict[str, Any], Path]:
    kind = BY_NAME.get(opts.type)
    if kind is None:
        raise Abort(USAGE, f"unknown type {opts.type!r}; see `testkeel list-types`")
    if kind.status != AVAILABLE:
        raise Abort(USAGE, f"type {opts.type!r} is planned, not available yet")
    slug = slugify(opts.name)
    if not slug:
        raise Abort(USAGE, f"--name {opts.name!r} has no letters or digits to make a name from")
    for flag, value in (("--target-url", opts.target_url), ("--api-base-url", opts.api_base_url)):
        if value is not None and not _URL_RE.match(value):
            raise Abort(USAGE, f"{flag} must be an http(s) URL")
    if opts.python_version not in {"3.11", "3.12", "3.13"}:
        raise Abort(USAGE, "--python must be 3.11, 3.12 or 3.13")
    if opts.report not in {"html", "allure", "both"}:
        raise Abort(USAGE, "--report must be html, allure or both")
    if opts.ci not in {"github", "none"}:
        raise Abort(USAGE, "--ci must be github or none (gitlab is planned)")
    summary = ""
    if opts.task_file is not None:
        if not opts.task_file.is_file():
            raise Abort(USAGE, f"--task-file {opts.task_file}: no such file")
        summary = task_summary(opts.task_file.read_text(encoding="utf-8", errors="replace"))
    output = (opts.output_dir or Path.cwd() / slug).resolve()
    if not output.parent.is_dir():
        raise Abort(USAGE, f"the parent of the output dir does not exist: {output.parent}")
    if output.exists() and (not output.is_dir() or any(output.iterdir())) and not opts.force:
        raise Abort(NOT_EMPTY, f"{output} is not empty; use --force to write into it")
    if opts.git and not opts.dry_run and shutil.which("git") is None:
        raise Abort(PREREQUISITE, "git is not on PATH (or pass --no-git)")
    url = getattr(opts, kind.url_option) if kind.url_option else None
    answers: dict[str, Any] = {
        "project_name": opts.name.strip(),
        "project_slug": slug,
        "company": opts.company.strip(),
        "task_summary": summary,
        "python_version": opts.python_version,
        "report": opts.report,
        "docker": opts.docker,
        "ci": opts.ci,
        "credit": opts.credit,
    }
    if kind.url_option:
        answers[kind.url_option] = url or kind.default_target
    return slug, answers, output


Renderer = Callable[[str, Path, Mapping[str, Any]], object]


def render(type_: str, staging: Path, answers: Mapping[str, Any]) -> None:
    """Render with the packaged templates (``testkeel.templates.render``).

    The answers file is never kept: projects are not ``copier update``-able yet
    (``updatable`` is always false), so it would only be a tool trace.
    """
    # Imported on use: the templates (and copier) load only when a project is rendered.
    templates = importlib.import_module("testkeel.templates")
    templates.render(type_, staging, answers)
    (staging / ANSWERS_FILE).unlink(missing_ok=True)


def _place_task(staging: Path, task_file: Path) -> None:
    target = staging / ".task" / "TASK.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(task_file, target)
    ignore = staging / ".gitignore"
    existing = ignore.read_text("utf-8") if ignore.exists() else ""
    if ".task/" not in existing.split():
        ignore.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n")
                          + ".task/\n", "utf-8")  # fmt: skip


def _tail(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-_OUTPUT_LINES:]


def lock(staging: Path, environ: Mapping[str, str]) -> str:
    """``uv lock`` in the staged tree against PyPI only; returns a one-line note.

    The default index is forced to PyPI and uv's config files and index variables are
    ignored, so a private registry from the user's machine never lands in ``uv.lock``.
    Raises :class:`StepFailed` when uv runs and fails.
    """
    if not (staging / "pyproject.toml").is_file():
        return "lock: nothing to lock (no pyproject.toml)"
    env = {**os.environ, **environ}
    uv = shutil.which("uv", path=env.get("PATH"))
    if uv is None:
        return "lock: skipped, uv is not on PATH; run `uv lock` in the project before you push"
    for name in _INDEX_VARS:
        env.pop(name, None)
    command = [uv, "lock", "--no-config", "--default-index", PYPI_SIMPLE, "--quiet"]
    result = subprocess.run(
        command, cwd=staging, env=env, capture_output=True, text=True, errors="replace"
    )
    if result.returncode != 0:
        raise StepFailed(
            f"uv lock (exit {result.returncode}); --no-lock skips it",
            _tail(result.stderr or result.stdout),
        )
    return "lock: uv.lock written (index: PyPI)"


def _python_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        found.extend(Path(directory) / name for name in files if name.endswith(".py"))
    return sorted(found)


def _ruff_command() -> list[str] | None:
    if importlib.util.find_spec("ruff") is not None:
        return [sys.executable, "-m", "ruff"]
    found = shutil.which("ruff")
    return [found] if found else None


def self_check(staging: Path) -> list[str]:
    """Every ``.py`` file compiles, and ``ruff check`` passes when ruff is available.

    Nothing is written into the staged tree (no bytecode, no ruff cache). Returns notes;
    raises :class:`StepFailed` with the failing step and its output.
    """
    files = _python_files(staging)
    for path in files:
        relative = path.relative_to(staging).as_posix()
        try:
            compile(path.read_bytes(), relative, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            where = f"{relative}:{getattr(exc, 'lineno', None) or 1}"
            raise StepFailed("compile", [f"{where}: {exc.__class__.__name__}: {exc}"]) from None
    if not files:
        return []
    ruff = _ruff_command()
    if ruff is None:
        return ["self-check: ruff is not available; lint skipped (compile only)"]
    result = subprocess.run(
        [*ruff, "check", "--no-cache", "--output-format", "concise", "."],
        cwd=staging,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        raise StepFailed("ruff check", _tail(result.stdout + result.stderr))
    return []


def _move(staging: Path, output: Path) -> None:
    if not output.exists():
        shutil.move(str(staging), str(output))
        return
    shutil.copytree(staging, output, dirs_exist_ok=True)
    shutil.rmtree(staging)


def _git_read(output: Path, env: Mapping[str, str], *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(output), *args], capture_output=True, text=True, env=dict(env)
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _git_init(output: Path, lines: list[str], env: Mapping[str, str]) -> None:
    """``git init`` only: no identity is written and no commit is made (D4).

    The identity git will use for the first commit (from the global config or the
    environment) is printed, unless it hits the denylist: then only a warning is printed,
    never the identity or the entry.
    """
    git_env = {**os.environ, **env}
    subprocess.run(["git", "init", "-q", "-b", "main", str(output)], check=True, env=git_env)
    ident = _git_read(output, git_env, "var", "GIT_AUTHOR_IDENT")
    values = [
        v
        for v in (
            _git_read(output, git_env, "config", "--get", "user.email"),
            _git_read(output, git_env, "config", "--get", "user.name"),
            ident,
        )
        if v
    ]
    if values and leakgate.denylist_hits(values, git_env, _home(git_env)):
        lines.append(
            "git: initialised; warning: your git identity matches a denylist entry. Set "
            "another one for this repository (git config user.name / user.email) before "
            "the first commit"
        )
    elif ident:
        lines.append(f"git: initialised; commits will be authored as {ident.rsplit('>', 1)[0]}>")
    else:
        lines.append("git: initialised; no git identity is configured yet")


def new_assignment(
    opts: Options,
    staging_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    renderer: Renderer | None = None,
) -> Result:
    started = time.monotonic()
    env = dict(os.environ if environ is None else environ)
    try:
        slug, answers, output = _validate(opts)
    except Abort as abort:
        return Result(abort.code, [f"error: {abort.message}"])
    root = staging_root or cache_dir(env) / "staging"
    root.mkdir(parents=True, exist_ok=True)
    # The staging name never carries the slug: on a leak it is printed, the slug may be the leak.
    staging = Path(tempfile.mkdtemp(prefix="render-", dir=root))
    lines: list[str] = []
    keep_staging = False
    try:
        (renderer or render)(opts.type, staging, answers)
        (staging / ANSWERS_FILE).unlink(missing_ok=True)
        if opts.task_file is not None:
            _place_task(staging, opts.task_file)
        files = sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file())
        if opts.dry_run:
            lines.append(f"dry run: {opts.type} project {slug!r} would be written to {output}")
            lines.extend(f"  {name}" for name in files)
            lines.append("answers: " + ", ".join(f"{k}={v!r}" for k, v in sorted(answers.items())))
            return Result(OK, lines)
        if opts.lock:
            try:
                lines.append(lock(staging, env))
            except StepFailed as failed:
                keep_staging = True
                return Result(SELF_CHECK, _step_failed(lines, failed, staging), staging=staging)
        else:
            lines.append("lock: skipped (--no-lock); run `uv lock` before you push")
        files = sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file())
        gate = leakgate.run(
            trees=[staging],
            texts=[("name", opts.name), ("slug", slug)],
            missing_list="warn",
            environ=env,
            project=ProjectRules.for_project(
                urls=[opts.target_url, opts.api_base_url, *_declared(answers)],
                home=_home(env),
            ),
        )
        lines.extend(f"leak gate: {note}" for note in gate.notes)
        if gate.code not in (leakgate.CLEAN,):
            keep_staging = True
            if gate.error:
                lines.append(f"leak gate: cannot run: {gate.error}")
            lines.extend(str(f) for f in gate.findings)
            lines.append(
                "leak gate failed: nothing was written to the output directory; the rendered "
                f"project is kept for inspection in {staging}"
            )
            return Result(LEAK, lines, gate.findings, staging)
        try:
            lines.extend(self_check(staging))
        except StepFailed as failed:
            keep_staging = True
            return Result(SELF_CHECK, _step_failed(lines, failed, staging), staging=staging)
        _move(staging, output)
        if opts.git and not (output / ".git").exists():
            _git_init(output, lines, env)
        took = time.monotonic() - started
        lines.insert(
            0,
            f"Created {slug} ({opts.type}) in {took:.1f} s; leak gate: 0 findings; "
            f"self-check: green; {len(files)} files in {output}",
        )
        lines += ["", "Next:", f"  cd {output}", "  $EDITOR README.md"]
        return Result(OK, lines, output=output)
    except KeyboardInterrupt:
        return Result(INTERRUPTED, ["interrupted"])
    except Exception:
        log = cache_dir(env) / "logs" / f"new-assignment-{int(time.time())}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        return Result(UNEXPECTED, [f"error: rendering failed; details in {log}"])
    finally:
        if not keep_staging:
            shutil.rmtree(staging, ignore_errors=True)


def _step_failed(lines: list[str], failed: StepFailed, staging: Path) -> list[str]:
    return [
        *lines,
        f"self-check failed at {failed.step}:",
        *(f"  {line}" for line in failed.detail),
        "nothing was written to the output directory; the rendered project is kept for "
        f"inspection in {staging}",
    ]


def _declared(answers: Mapping[str, Any]) -> list[str]:
    return [str(answers[key]) for key in ("target_url", "api_base_url") if answers.get(key)]


def _home(env: Mapping[str, str]) -> Path | None:
    return Path(env["HOME"]) if env.get("HOME") else None


def check(
    path: Path,
    strict: bool = False,
    environ: Mapping[str, str] | None = None,
    allow_hosts: Sequence[str] = (),
) -> Result:
    """``assignment check``: the leak gate over an existing project (0 / 4 / 2).

    The project rules run too. The project's own target host is not known here, so it
    comes from ``allow_hosts`` (``--allow-host``) or ``~/.config/testkeel/allow-hosts.txt``.
    """
    if not path.is_dir():
        return Result(USAGE, [f"error: {path} is not a directory"])
    env = dict(os.environ if environ is None else environ)
    rules = ProjectRules.for_project(extra_hosts=allow_hosts, home=_home(env))
    gate = leakgate.run(
        trees=[path], missing_list="fail" if strict else "warn", environ=env, project=rules
    )
    lines = [f"leak gate: {note}" for note in gate.notes]
    if gate.code == leakgate.CANNOT_RUN:
        return Result(USAGE, [*lines, f"leak gate: cannot run: {gate.error}"])
    lines.extend(str(f) for f in gate.findings)
    if gate.findings:
        return Result(LEAK, [*lines, f"leak gate: {len(gate.findings)} finding(s)"], gate.findings)
    return Result(OK, [*lines, "leak gate: clean"])
