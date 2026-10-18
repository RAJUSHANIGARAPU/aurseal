"""``assignment update``: bring template changes from a newer release into a project.

A project made with ``--updatable`` records its type, its answers and the release tag it
was rendered from (``.copier-answers.yml``). This command renders the same type and answers
twice, from the templates at the recorded tag and at the target tag, and applies the
difference between the two renders to the project with a three-way merge
(``git merge-file``), file by file: Copier's update semantics without ``copier update``,
which cannot replay the multi-pass render (skeleton, then overlays).

Both renders use this installation's render passes over the template files of each tag,
and run no code from the fetched tags. The project must be a git repository with nothing
uncommitted, so every change the update makes can be reviewed (``git diff``) and undone.

Exit codes: 0 updated (or already at the target), 1 unexpected error, 2 usage error,
3 updated with conflicts (``<<<<<<<`` markers left in the named files), 6 git missing or
the template source or tag cannot be fetched.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aurseal.assignment.scaffold import (
    ANSWERS_FILE,
    OK,
    PREREQUISITE,
    TEMPLATE_TAG,
    UNEXPECTED,
    USAGE,
    Result,
    cache_dir,
)

CONFLICT = 3
#: Where the templates live inside the template source repository.
TEMPLATES_PATH = "src/aurseal/templates"


class UpdateError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def read_answers(text: str) -> dict[str, Any]:
    """The answers file as written by ``new-assignment --updatable``: one ``key: value``
    per line, values as JSON scalars (``_commit`` and ``_src_path`` bare)."""
    answers: dict[str, Any] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, raw = (part.strip() for part in line.split(":", 1))
        try:
            answers[key] = json.loads(raw)
        except ValueError:
            answers[key] = raw.strip("'\"")
    return answers


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env)


def _tail(raw: bytes) -> str:
    lines = [line for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    return "; ".join(lines[-3:])


def _check_project(project: Path) -> None:
    inside = _git("rev-parse", "--is-inside-work-tree", cwd=project)
    if inside.returncode != 0:
        raise UpdateError(
            USAGE,
            f"{project} is not a git repository; commit the project first, so the update "
            "can be reviewed and undone",
        )
    status = _git("status", "--porcelain", "--untracked-files=all", "--", ".", cwd=project)
    if status.returncode != 0 or status.stdout.strip():
        raise UpdateError(USAGE, f"{project} has uncommitted changes; commit or stash them first")


def _fetch_templates(source: str, tags: list[str], work: Path) -> dict[str, Path]:
    """Each tag's ``src/aurseal/templates`` from ``source``, extracted under ``work``."""
    repo = work / "source.git"
    if _git("init", "-q", "--bare", str(repo)).returncode != 0:
        raise UpdateError(UNEXPECTED, "git init failed in the cache directory")
    found: dict[str, Path] = {}
    for tag in tags:
        fetched = _git(
            "--git-dir", str(repo), "fetch", "-q", "--depth", "1", "--no-tags",
            source, f"refs/tags/{tag}:refs/tags/{tag}",
        )  # fmt: skip
        if fetched.returncode != 0:
            raise UpdateError(
                PREREQUISITE, f"cannot fetch tag {tag} from {source}: {_tail(fetched.stderr)}"
            )
        archive = _git("--git-dir", str(repo), "archive", "--format=tar", tag, TEMPLATES_PATH)
        if archive.returncode != 0:
            raise UpdateError(USAGE, f"tag {tag} of {source} has no {TEMPLATES_PATH}/")
        target = work / tag
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            if hasattr(tarfile, "data_filter"):  # Python 3.11.4 and later
                tar.extractall(target, filter="data")
            else:  # pragma: no cover
                tar.extractall(target)
        found[tag] = target / TEMPLATES_PATH
    return found


def _render(type_: str, templates_dir: Path, answers: Mapping[str, Any], out: Path) -> None:
    from aurseal import templates  # imports copier: only when a project is rendered

    data = {k: v for k, v in answers.items() if k in templates.DATA_KEYS}
    templates.render(type_, out, data, templates_dir=templates_dir)


def _files(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).as_posix() != ANSWERS_FILE
    }


def _same(a: Path, b: Path) -> bool:
    return a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes()


def _write(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    shutil.copymode(source, target)


def _remove(target: Path, project: Path) -> None:
    target.unlink()
    parent = target.parent
    while parent != project and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _merge(mine: Path, base: Path | None, theirs: Path, labels: tuple[str, str], work: Path) -> int:
    """Three-way merge into ``mine``; returns the number of conflicts (-1: cannot merge)."""
    if base is None:  # added by the new release and by you: merge against an empty file
        base = work / "empty"
        base.write_bytes(b"")
    result = _git(
        "merge-file", "-L", "yours", "-L", labels[0], "-L", labels[1],
        str(mine), str(base), str(theirs),
    )  # fmt: skip
    return result.returncode if result.returncode < 128 else -1


def apply(project: Path, old: Path, new: Path, labels: tuple[str, str], work: Path) -> Result:
    """Apply the change from render ``old`` to render ``new`` onto ``project``."""
    lines: list[str] = []
    conflicts: list[str] = []
    old_files, new_files = _files(old), _files(new)
    for name in sorted(old_files | new_files):
        before = old / name if name in old_files else None
        after = new / name if name in new_files else None
        here = project / name
        if before is not None and after is not None and _same(before, after):
            continue  # the template did not change this file
        if after is None:
            assert before is not None
            if not here.is_file():
                continue
            if _same(here, before):
                _remove(here, project)
                lines.append(f"  removed   {name}")
            else:
                lines.append(f"  kept      {name} (removed by {labels[1]}, but you changed it)")
            continue
        if not here.exists():
            if before is not None:
                lines.append(f"  skipped   {name} (you deleted it; {labels[1]} changes it)")
            else:
                _write(after, here)
                lines.append(f"  added     {name}")
            continue
        if _same(here, after):
            continue  # you already have the new content
        if before is not None and _same(here, before):
            _write(after, here)
            lines.append(f"  updated   {name}")
            continue
        count = _merge(here, before, after, labels, work)
        if count == 0:
            lines.append(f"  merged    {name}")
        elif count > 0:
            conflicts.append(name)
            lines.append(f"  CONFLICT  {name} ({count} conflicting hunk(s), markers left)")
        else:
            conflicts.append(name)
            lines.append(f"  CONFLICT  {name} (cannot be merged as text; kept yours)")
    code = CONFLICT if conflicts else OK
    return Result(code, lines)


def _bump(answers_file: Path, tag: str) -> None:
    text = answers_file.read_text("utf-8")
    lines = [f"_commit: {tag}" if ln.startswith("_commit:") else ln for ln in text.splitlines()]
    answers_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update(
    project: Path,
    to: str | None = None,
    source: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Result:
    env = dict(os.environ if environ is None else environ)
    project = project.resolve()
    answers_file = project / ANSWERS_FILE
    if not answers_file.is_file():
        return Result(
            USAGE,
            [
                f"error: {project} has no {ANSWERS_FILE}; only a project made with "
                "`new-assignment --updatable` can be updated"
            ],
        )
    answers = read_answers(answers_file.read_text("utf-8"))
    current, type_ = answers.get("_commit"), answers.get("type")
    src = source or answers.get("_src_path")
    if not (isinstance(current, str) and isinstance(type_, str) and isinstance(src, str)):
        return Result(USAGE, [f"error: {ANSWERS_FILE} lacks _commit, _src_path or type"])
    target = to or TEMPLATE_TAG
    if target == current:
        return Result(OK, [f"already at {current}; nothing to update"])
    if shutil.which("git") is None:
        return Result(PREREQUISITE, ["error: git is not on PATH"])
    root = cache_dir(env) / "update"
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="update-", dir=root))
    try:
        _check_project(project)
        dirs = _fetch_templates(src, [current, target], work)
        renders = {}
        for tag in (current, target):
            renders[tag] = work / "render" / tag
            _render(type_, dirs[tag], answers, renders[tag])
        result = apply(project, renders[current], renders[target], (current, target), work)
        _bump(answers_file, target)
        changed = len(result.lines)
        head = f"Updated {project.name} ({type_}) from {current} to {target}: "
        head += f"{changed} file(s) changed" if changed else "no template changes in between"
        lines = [head, *result.lines]
        if any(line.split()[1:2] == ["pyproject.toml"] for line in result.lines):
            lines.append("pyproject.toml changed: run `uv lock` before you commit.")
        if result.code == CONFLICT:
            lines += [
                "",
                f"{sum('CONFLICT' in line for line in result.lines)} conflict(s): resolve the "
                "<<<<<<< markers in the files above, then commit.",
            ]
        lines += [
            "Review with `git diff`; undo the whole update with "
            "`git checkout -- . && git clean -fd`.",
        ]
        return Result(result.code, lines)
    except UpdateError as failed:
        return Result(failed.code, [f"error: {failed.message}"])
    except Exception:
        log = cache_dir(env) / "logs" / f"update-{os.getpid()}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        return Result(UNEXPECTED, [f"error: the update failed; details in {log}"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
