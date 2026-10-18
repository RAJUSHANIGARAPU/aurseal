"""``new-assignment``: validate, render into a staging dir, leak-gate it, then move it.

Nothing reaches the output directory before the staged tree passes the leak gate. On a
leak-gate failure the staged tree is kept (its path is printed) so the finding can be
inspected; on any other failure the staging dir is removed.

Exit codes: 0 created, 1 unexpected error (traceback written to a log file), 2 usage
error, 3 output dir not empty without ``--force``, 4 leak gate failed, 6 missing
prerequisite, 130 interrupted.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testkeel import leakgate
from testkeel.assignment.slug import slugify
from testkeel.assignment.types import AVAILABLE, BY_NAME
from testkeel.leakgate.scan import Finding

OK, UNEXPECTED, USAGE, NOT_EMPTY, LEAK, PREREQUISITE, INTERRUPTED = 0, 1, 2, 3, 4, 6, 130
TEMPLATES_ROOT = Path(__file__).resolve().parents[1] / "templates"
ANSWERS_FILE = ".copier-answers.yml"
_URL_RE = re.compile(r"^https?://[^\s/]+[^\s]*$")


class Abort(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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
    if opts.ci not in {"github", "gitlab", "none"}:
        raise Abort(USAGE, "--ci must be github, gitlab or none")
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


def render(type_: str, staging: Path, answers: Mapping[str, Any], templates_root: Path) -> None:
    """Two Copier passes into ``staging``: the common skeleton, then the type overlay.

    ``templates_root`` holds one ``copier.yml`` whose ``_subdirectory`` routes on the
    ``layer`` answer. No ``_tasks`` and no extensions: ``unsafe`` stays off.
    """
    from copier import run_copy

    data = {**answers, "type": type_}
    for layer in ("base", "overlay"):
        run_copy(
            str(templates_root),
            str(staging),
            data={**data, "layer": layer},
            defaults=True,
            overwrite=True,
            quiet=True,
            unsafe=False,
        )
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


def _move(staging: Path, output: Path) -> None:
    if not output.exists():
        shutil.move(str(staging), str(output))
        return
    shutil.copytree(staging, output, dirs_exist_ok=True)
    shutil.rmtree(staging)


def _git_init(output: Path, lines: list[str]) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(output)], check=True)
    who = subprocess.run(
        ["git", "-C", str(output), "var", "GIT_AUTHOR_IDENT"], capture_output=True, text=True
    )
    if who.returncode == 0:
        ident = who.stdout.rsplit(">", 1)[0] + ">"
        lines.append(f"git: initialised; commits will be authored as {ident}")
    else:
        lines.append("git: initialised; no git identity is configured yet")


def new_assignment(
    opts: Options,
    templates_root: Path | None = None,
    staging_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    renderer: Callable[[str, Path, Mapping[str, Any], Path], None] = render,
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
        renderer(opts.type, staging, answers, templates_root or TEMPLATES_ROOT)
        if opts.task_file is not None:
            _place_task(staging, opts.task_file)
        files = sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file())
        if opts.dry_run:
            lines.append(f"dry run: {opts.type} project {slug!r} would be written to {output}")
            lines.extend(f"  {name}" for name in files)
            lines.append("answers: " + ", ".join(f"{k}={v!r}" for k, v in sorted(answers.items())))
            return Result(OK, lines)
        gate = leakgate.run(
            trees=[staging],
            texts=[("name", opts.name), ("slug", slug)],
            missing_list="warn",
            environ=env,
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
        _move(staging, output)
        if opts.git and not (output / ".git").exists():
            _git_init(output, lines)
        took = time.monotonic() - started
        lines.insert(
            0,
            f"Created {slug} ({opts.type}) in {took:.1f} s; leak gate: 0 findings; "
            f"{len(files)} files in {output}",
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


def check(path: Path, strict: bool = False, environ: Mapping[str, str] | None = None) -> Result:
    """``assignment check``: the leak gate over an existing project (0 / 4 / 2)."""
    if not path.is_dir():
        return Result(USAGE, [f"error: {path} is not a directory"])
    gate = leakgate.run(trees=[path], missing_list="fail" if strict else "warn", environ=environ)
    lines = [f"leak gate: {note}" for note in gate.notes]
    if gate.code == leakgate.CANNOT_RUN:
        return Result(USAGE, [*lines, f"leak gate: cannot run: {gate.error}"])
    lines.extend(str(f) for f in gate.findings)
    if gate.findings:
        return Result(LEAK, [*lines, f"leak gate: {len(gate.findings)} finding(s)"], gate.findings)
    return Result(OK, [*lines, "leak gate: clean"])
