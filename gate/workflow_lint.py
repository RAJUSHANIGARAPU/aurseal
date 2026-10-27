"""Offline lint for GitHub Actions workflows.

A workflow is only ever executed by the CI service, so a broken edit is
discovered after it has already blinded CI. This does not try to be the CI
service's parser. It checks the cheap, decidable properties whose violation
has actually broken pipelines:

* the file parses as YAML and is a mapping with ``on`` and ``jobs``;
* every job has ``runs-on`` (or ``uses`` for a reusable workflow) and a
  non-empty ``steps`` list, and every step has exactly one of ``run``/``uses``;
* no ``env`` value is empty — ``KEY:`` with nothing after it is YAML null, the
  workflow equivalent of an assignment that lost its value;
* ``${{ ... }}`` expressions are closed;
* ``needs`` names jobs that exist;
* no pytest command masks its exit status (``|| true``, ``|| [ $? -eq 5 ]``),
  which would turn "selected nothing" or "failed" into green;
* the jobs a caller requires are present.

Shape rules (:class:`ShapeOptions`, on by default for the CLI and
:func:`lint_workflows`) check what a reviewer would otherwise have to remember:

* a top-level ``concurrency`` group whose ``cancel-in-progress`` cannot cancel a
  default-branch run (an expression naming ``pull_request`` passes);
* every job declares ``timeout-minutes``, and the longest ``needs`` chain fits
  the whole-run budget (the critical path is named when it does not);
* every ``upload-artifact`` step declares ``retention-days``, and heavy evidence
  (traces, videos, screenshots, report trees) keeps it short;
* a browser container is pinned to an exact Playwright tag equal to the
  requirements pin, gets the shared memory Chromium needs, and is branch-gated
  at job level (a step-level ``github.ref`` condition still pulls the image);
* a ``schedule`` cron is off the hour, and in a scheduled workflow every job
  but the secret scan reads the nightly switch variable (``NIGHTLY_ENABLED``);
* a job that runs the secret scan has no ``if:``, no ``needs:``, a full-history
  checkout and no masked exit status;
* a dispatch-triggered workflow (``repository_dispatch``/``workflow_dispatch``) does not
  share a concurrency group with ``tests.yml``, so an externally triggered run never
  queues behind (or replaces a pending) merge gate.
* a step that composes the release verdict (``traceability release decide``) in an
  ordinary workflow (one triggered by pull requests or a schedule) is report only: its
  ``if:`` contains ``always()`` (it must run after a red
  or aborted lane), it passes ``--enforcement report``, and it does not set
  ``continue-on-error: false``.

``python -m gate.workflow_lint [files...]`` is the local parse check and the
commit guard: the pre-commit hook scoped to ``.github/workflows/`` runs it on
every staged workflow, so a broken workflow fails before it is pushed.
Exit 0 clean, 1 findings, 2 a named file is missing, 3 ``--actionlint`` was
asked for and is not installed (an abstention, reported as such, never green).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_MASK_RE = re.compile(r"\|\|\s*(true|:|exit\s+0|\[\s*\$\?\s*-eq\s*5\s*\])")
_PLAYWRIGHT_TAG_RE = re.compile(r"playwright/python:v(\d+\.\d+\.\d+)(?:-[a-z0-9.]+)?$")
_PIN_RE = re.compile(r"^\s*playwright\s*==\s*([0-9][0-9.]*)\s*(?:#.*)?$", re.MULTILINE)
_SHM_RE = re.compile(r"--shm-size[= ](\d+(?:\.\d+)?)([kmgKMG]?)[bB]?")
SECRET_SCAN_TOOL = "gitleaks"
NIGHTLY_VARIABLE = "NIGHTLY_ENABLED"


@dataclass(frozen=True)
class Finding:
    file: str
    where: str
    problem: str

    def __str__(self) -> str:
        return f"{self.file}: {self.where}: {self.problem}"


def _on(data: dict) -> object:
    # PyYAML (YAML 1.1) reads a bare `on:` key as the boolean True.
    return data.get("on", data.get(True))


def _check_env(file: str, where: str, env: object, out: list[Finding]) -> None:
    if not isinstance(env, dict):
        return
    for key, value in env.items():
        if value is None:
            out.append(Finding(file, where, f"env {key} has no value"))


def _check_expressions(file: str, where: str, text: str, out: list[Finding]) -> None:
    if text.count("${{") != text.count("}}"):
        out.append(Finding(file, where, "unbalanced ${{ }} expression"))


@dataclass(frozen=True)
class ShapeOptions:
    """Thresholds for the shape rules; every one is a CLI flag as well."""

    run_budget_minutes: int = 60
    short_retention_days: int = 7
    long_retention_days: int = 30
    heavy_paths: tuple[str, ...] = ("traces", "videos", "screenshots", "reports", "evidence")
    requirements: Path | None = None  # default: <repo>/requirements.txt next to .github/
    # A workflow that is one long job by nature gets its own, visible budget.
    budget_overrides: tuple[tuple[str, int], ...] = (("mutation.yml", 90),)
    nightly_variable: str = NIGHTLY_VARIABLE

    def budget_for(self, file_name: str) -> int:
        return dict(self.budget_overrides).get(file_name, self.run_budget_minutes)


DEFAULT_SHAPE = ShapeOptions()


def playwright_pin(requirements: str | Path) -> str | None:
    """The ``playwright==X`` version a requirements file pins, or None."""
    try:
        match = _PIN_RE.search(Path(requirements).read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def image_playwright_version(image: str) -> str | None:
    """``1.58.0`` for ``mcr.microsoft.com/playwright/python:v1.58.0-jammy``; None otherwise."""
    match = _PLAYWRIGHT_TAG_RE.search(image.strip())
    return match.group(1) if match else None


def check_playwright_image(image: str, pin: str | None) -> list[str]:
    """Problems with a Playwright image reference against the requirements pin."""
    version = image_playwright_version(image)
    if version is None:
        return [f"image {image!r} is not pinned to an exact Playwright version tag"]
    if pin is None:
        return ["cannot read the playwright== pin from the requirements file"]
    if version != pin:
        return [f"image Playwright {version} differs from requirements playwright=={pin}"]
    return []


def _steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps") or [] if isinstance(s, dict)]


def _uses(step: dict, action: str) -> bool:
    return str(step.get("uses") or "").split("@")[0] == action


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else [str(n) for n in needs]


def critical_path(jobs: dict) -> tuple[list[str], int]:
    """The longest ``needs`` chain by summed ``timeout-minutes`` (missing counts 0)."""
    best: dict[str, tuple[int, list[str]]] = {}

    def visit(name: str, trail: tuple[str, ...]) -> tuple[int, list[str]]:
        if name in best:
            return best[name]
        job = jobs.get(name)
        if not isinstance(job, dict) or name in trail:
            return 0, []
        own = job.get("timeout-minutes")
        minutes = int(own) if isinstance(own, int) else 0
        longest: tuple[int, list[str]] = (0, [])
        for need in _needs(job):
            candidate = visit(need, (*trail, name))
            if candidate[0] > longest[0]:
                longest = candidate
        best[name] = (longest[0] + minutes, [*longest[1], name])
        return best[name]

    total, path = 0, []
    for name in jobs:
        minutes, chain = visit(name, ())
        if minutes > total:
            total, path = minutes, chain
    return path, total


def _is_secret_scan(job: dict) -> bool:
    return any(SECRET_SCAN_TOOL + " git" in str(s.get("run") or "") for s in _steps(job))


def _cancel_problem(concurrency: object) -> str | None:
    if concurrency is None:
        return "has no top-level concurrency group"
    if isinstance(concurrency, str):
        return None
    if not isinstance(concurrency, dict):
        return "concurrency is neither a group name nor a mapping"
    cancel = concurrency.get("cancel-in-progress", False)
    if cancel is True:
        return "concurrency cancels in-progress runs on every ref, the default branch included"
    if isinstance(cancel, str) and "pull_request" not in cancel:
        return "concurrency cancel-in-progress must be false on the default branch"
    return None


def _shm_ok(options: str) -> bool:
    if "--ipc=host" in options or "--ipc host" in options:
        return True
    match = _SHM_RE.search(options)
    if not match:
        return False
    size, unit = float(match.group(1)), match.group(2).lower()
    gigabytes = {"": size / 1024**3, "k": size / 1024**2, "m": size / 1024, "g": size}[unit]
    return gigabytes >= 1


def _schedule_crons(data: dict) -> list[str]:
    on = _on(data)
    if not isinstance(on, dict):
        return []
    return [
        str(entry.get("cron"))
        for entry in on.get("schedule") or []
        if isinstance(entry, dict) and entry.get("cron")
    ]


def _lint_shape(
    file: str, path: Path, data: dict, jobs: dict, shape: ShapeOptions, out: list[Finding]
) -> None:
    problem = _cancel_problem(data.get("concurrency"))
    if problem:
        out.append(Finding(file, "workflow", problem))

    budget = shape.budget_for(path.name)
    chain, minutes = critical_path(jobs)
    if minutes > budget:
        out.append(
            Finding(
                file,
                "workflow",
                f"critical path {', '.join(chain)} = {minutes} min exceeds the run budget "
                f"of {budget} min",
            )
        )

    crons = _schedule_crons(data)
    for cron in crons:
        if cron.split()[0] in {"0", "00"}:
            out.append(Finding(file, "on.schedule", f"cron {cron!r} fires on the hour"))
    switch = f"vars.{shape.nightly_variable}" if shape.nightly_variable else None

    requirements = shape.requirements or path.resolve().parents[2] / "requirements.txt"
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        where = f"jobs.{job_name}"
        if "uses" not in job and not isinstance(job.get("timeout-minutes"), int):
            out.append(Finding(file, where, "has no timeout-minutes"))
        secret_scan = _is_secret_scan(job)
        # A job's `if:` (or, for the job reporting the switch, its steps) must read it.
        if crons and switch and not secret_scan and switch not in str(job):
            out.append(Finding(file, where, f"is not guarded by the {switch} nightly switch"))
        if secret_scan:
            _lint_secret_scan(file, where, job, out)
        _lint_container(file, where, job, requirements, out)
        for index, step in enumerate(_steps(job)):
            if _uses(step, "actions/upload-artifact"):
                _lint_upload(file, f"{where}.steps[{index}]", step, shape, out)
    # Only an ordinary CI workflow (pull requests or a schedule); the dedicated release
    # workflow is the one place the verdict is allowed to fail the run.
    on = _on(data)
    triggers = set(on) if isinstance(on, (dict, list)) else {on}
    if triggers & {"pull_request", "schedule"}:
        for where, step in release_verdict_steps(jobs):
            out += [Finding(file, where, p) for p in release_verdict_problems(step)]


RELEASE_DECIDE = "traceability release decide"


def release_verdict_steps(jobs: dict) -> list[tuple[str, dict]]:
    """``(where, step)`` for every step that runs ``traceability release decide``."""
    found = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(_steps(job)):
            if RELEASE_DECIDE in str(step.get("run") or ""):
                found.append((f"jobs.{job_name}.steps[{index}]", step))
    return found


def release_verdict_problems(step: dict) -> list[str]:
    """Why a release-verdict step on an ordinary run could change the build result."""
    problems = []
    if "always()" not in str(step.get("if") or ""):
        problems.append("release verdict step must run with always() (after a red lane too)")
    if "--enforcement report" not in str(step.get("run") or ""):
        problems.append("release verdict step must pass --enforcement report")
    if step.get("continue-on-error") is False:
        problems.append("release verdict step must not set continue-on-error: false")
    return problems


def _lint_upload(file: str, where: str, step: dict, shape: ShapeOptions, out: list) -> None:
    with_ = step.get("with") or {}
    days = with_.get("retention-days")
    if days is None:
        out.append(Finding(file, where, "uploads an artefact without retention-days"))
        return
    try:
        days = int(days)
    except (TypeError, ValueError):
        return  # an expression; the CI service validates it
    paths = str(with_.get("path") or "")
    segments = {seg for line in paths.splitlines() for seg in line.strip().split("/") if seg}
    heavy = sorted(segments & set(shape.heavy_paths))
    if heavy and days > shape.short_retention_days:
        out.append(
            Finding(
                file,
                where,
                f"heavy artefact ({', '.join(heavy)}) kept {days} days; "
                f"limit {shape.short_retention_days}",
            )
        )
    elif days > shape.long_retention_days:
        out.append(
            Finding(file, where, f"artefact kept {days} days; limit {shape.long_retention_days}")
        )


def _lint_container(file: str, where: str, job: dict, requirements: Path, out: list) -> None:
    container = job.get("container")
    if container is None:
        return
    image = container if isinstance(container, str) else str(container.get("image") or "")
    if "playwright" not in image:
        return
    for problem in check_playwright_image(image, playwright_pin(requirements)):
        out.append(Finding(file, where, problem))
    options = "" if isinstance(container, str) else str(container.get("options") or "")
    if not _shm_ok(options):
        out.append(
            Finding(file, where, "browser container needs --ipc=host or --shm-size of 1g or more")
        )
    for index, step in enumerate(_steps(job)):
        if "github.ref" in str(step.get("if") or ""):
            out.append(
                Finding(
                    file,
                    f"{where}.steps[{index}]",
                    "branch condition on a container step: gate the job instead, so a skipped "
                    "run never pulls the image",
                )
            )


def _lint_secret_scan(file: str, where: str, job: dict, out: list) -> None:
    if "if" in job:
        out.append(Finding(file, where, "secret scan job has an if: condition"))
    if job.get("needs"):
        out.append(Finding(file, where, "secret scan job has needs:"))
    if job.get("continue-on-error"):
        out.append(Finding(file, where, "secret scan job has continue-on-error"))
    checkouts = [s for s in _steps(job) if _uses(s, "actions/checkout")]
    if not checkouts or any((s.get("with") or {}).get("fetch-depth") != 0 for s in checkouts):
        out.append(Finding(file, where, "secret scan checkout must use fetch-depth: 0"))
    for index, step in enumerate(_steps(job)):
        run = str(step.get("run") or "")
        if SECRET_SCAN_TOOL + " git" not in run:
            continue
        step_where = f"{where}.steps[{index}]"
        if step.get("continue-on-error") or "if" in step:
            out.append(Finding(file, step_where, "secret scan step can be skipped or ignored"))
        if _MASK_RE.search(run):
            out.append(Finding(file, step_where, "secret scan masks its exit status"))


GATE_WORKFLOW = "tests.yml"
DISPATCH_EVENTS = ("repository_dispatch", "workflow_dispatch")
_EXPR_RE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def _group_text(data: dict) -> str | None:
    concurrency = data.get("concurrency")
    if isinstance(concurrency, str):
        return concurrency
    if isinstance(concurrency, dict) and concurrency.get("group") is not None:
        return str(concurrency["group"])
    return None


def _render_workflow_name(group: str, name: str) -> str:
    return _EXPR_RE.sub(lambda m: name if m.group(1) == "github.workflow" else m.group(0), group)


def groups_collide(gate_group: str, gate_name: str, other_group: str, other_name: str) -> bool:
    """Whether ``other_group`` can evaluate to a value ``gate_group`` can take.

    ``github.workflow`` is replaced by each workflow's own name; any other expression of the
    gate's group matches anything. Two groups that still hold expressions collide when their
    texts are equal once the names are filled in.
    """
    gate = _render_workflow_name(gate_group, gate_name)
    other = _render_workflow_name(other_group, other_name)
    if _EXPR_RE.search(other):
        return gate == other
    pattern = "".join(
        ".*" if part.startswith("${{") else re.escape(part)
        for part in re.split(r"(\$\{\{.*?\}\})", gate)
        if part
    )
    return re.fullmatch(pattern, other) is not None


def shared_gate_concurrency(directory: str | Path) -> list[Finding]:
    """Findings for dispatch-triggered workflows whose concurrency group can be the gate's."""
    base = Path(directory)
    gate_path = base / GATE_WORKFLOW
    try:
        gate = yaml.safe_load(gate_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    gate_group = _group_text(gate) if isinstance(gate, dict) else None
    if gate_group is None:
        return []
    findings = []
    for file in sorted([*base.glob("*.yml"), *base.glob("*.yaml")]):
        if file.name == GATE_WORKFLOW:
            continue
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        on = _on(data)
        events = set(on) if isinstance(on, (dict, list)) else {on}
        if not events & set(DISPATCH_EVENTS):
            continue
        group = _group_text(data)
        if group is not None and groups_collide(
            gate_group, str(gate.get("name", "")), group, str(data.get("name", ""))
        ):
            findings.append(
                Finding(
                    str(file),
                    "concurrency",
                    f"dispatch-triggered workflow shares the {GATE_WORKFLOW} concurrency group "
                    f"({group!r}): it would queue behind the merge gate",
                )
            )
    return findings


def missing_secret_scan(directory: str | Path) -> list[Finding]:
    """A finding when no workflow under ``directory`` has a secret-scan job."""
    for file in sorted([*Path(directory).glob("*.yml"), *Path(directory).glob("*.yaml")]):
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if isinstance(jobs, dict) and any(
            isinstance(j, dict) and _is_secret_scan(j) for j in jobs.values()
        ):
            return []
    return [Finding(str(directory), "workflows", "no job runs the secret scan")]


def lint_workflow(
    path: str | Path, required_jobs: Iterable[str] = (), shape: ShapeOptions | None = None
) -> list[Finding]:
    """Structure rules always; the shape rules too when ``shape`` is given."""
    file = str(path)
    findings: list[Finding] = []
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [Finding(file, "file", f"does not parse: {exc}")]
    if not isinstance(data, dict):
        return [Finding(file, "file", "is not a mapping")]
    if _on(data) is None:
        findings.append(Finding(file, "file", "has no 'on' trigger"))
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        findings.append(Finding(file, "file", "has no jobs"))
        return findings
    _check_env(file, "workflow", data.get("env"), findings)
    _check_expressions(file, "file", text, findings)

    for name in required_jobs:
        if name not in jobs:
            findings.append(Finding(file, f"jobs.{name}", "required job is missing"))

    for job_name, job in jobs.items():
        where = f"jobs.{job_name}"
        if not isinstance(job, dict):
            findings.append(Finding(file, where, "is not a mapping"))
            continue
        needs = job.get("needs") or []
        for need in [needs] if isinstance(needs, str) else needs:
            if need not in jobs:
                findings.append(Finding(file, where, f"needs unknown job {need!r}"))
        if "uses" in job:
            continue
        if "runs-on" not in job:
            findings.append(Finding(file, where, "has no runs-on"))
        _check_env(file, where, job.get("env"), findings)
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            findings.append(Finding(file, where, "has no steps"))
            continue
        for index, step in enumerate(steps):
            step_where = f"{where}.steps[{index}]"
            if not isinstance(step, dict):
                findings.append(Finding(file, step_where, "is not a mapping"))
                continue
            if ("run" in step) == ("uses" in step):
                findings.append(Finding(file, step_where, "needs exactly one of run/uses"))
            if "run" in step and (step["run"] is None or not str(step["run"]).strip()):
                findings.append(Finding(file, step_where, "has an empty run"))
            _check_env(file, step_where, step.get("env"), findings)
            for line in str(step.get("run") or "").splitlines():
                if "pytest" in line and _MASK_RE.search(line):
                    findings.append(
                        Finding(file, step_where, f"masks the pytest exit status: {line.strip()}")
                    )
    if shape is not None:
        _lint_shape(file, Path(path), data, jobs, shape, findings)
    return findings


def lint_workflows(
    directory: str | Path = ".github/workflows",
    required: dict[str, Iterable[str]] | None = None,
    shape: ShapeOptions | None = DEFAULT_SHAPE,
) -> list[Finding]:
    """Lint every ``*.yml``/``*.yaml`` under ``directory``; ``required`` maps file name to jobs."""
    required = required or {}
    findings: list[Finding] = []
    base = Path(directory)
    files = sorted([*base.glob("*.yml"), *base.glob("*.yaml")])
    for name in required:
        if not (base / name).is_file():
            findings.append(Finding(str(base / name), "file", "required workflow is missing"))
    for file in files:
        findings.extend(lint_workflow(file, required.get(file.name, ()), shape))
    if shape is not None and files:
        findings.extend(missing_secret_scan(base))
        findings.extend(shared_gate_concurrency(base))
    return findings


EXIT_OK, EXIT_FINDINGS, EXIT_MISSING, EXIT_ABSTAINED = 0, 1, 2, 3


@dataclass(frozen=True)
class CheckResult:
    """``status``: ``ok``, ``failed``, ``missing`` or ``abstained``."""

    status: str
    findings: list[Finding] = field(default_factory=list)
    detail: str = ""

    @property
    def exit_code(self) -> int:
        return {
            "ok": EXIT_OK,
            "failed": EXIT_FINDINGS,
            "missing": EXIT_MISSING,
            "abstained": EXIT_ABSTAINED,
        }[self.status]


def parse_check(
    path: str | Path, required_jobs: Iterable[str] = (), shape: ShapeOptions | None = DEFAULT_SHAPE
) -> CheckResult:
    """Parse one workflow and check its structure and shape. Never raises on a bad file."""
    target = Path(path)
    if not target.is_file():
        return CheckResult("missing", detail=f"{target}: no such workflow file")
    findings = lint_workflow(target, required_jobs, shape)
    return CheckResult("failed" if findings else "ok", findings)


def actionlint_check(
    paths: Sequence[str | Path],
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> CheckResult:
    """Run actionlint when it is installed; abstain (not pass) when it is not."""
    binary = (which or shutil.which)("actionlint")
    if binary is None:
        return CheckResult("abstained", detail="actionlint is not installed: not checked")
    completed = runner(
        [binary, "-no-color", *map(str, paths)], capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return CheckResult("ok")
    findings = [
        Finding("actionlint", "output", line.strip())
        for line in (completed.stdout + completed.stderr).splitlines()
        if line.strip()
    ]
    return CheckResult("failed", findings or [Finding("actionlint", "exit", "non-zero exit")])


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m gate.workflow_lint")
    parser.add_argument("files", nargs="*", help="workflow files (default: every workflow)")
    parser.add_argument("--dir", default=".github/workflows")
    parser.add_argument("--actionlint", action="store_true", help="also run actionlint")
    parser.add_argument(
        "--structure-only", action="store_true", help="skip the shape rules (budget, retention...)"
    )
    defaults = DEFAULT_SHAPE
    parser.add_argument("--run-budget", type=int, default=defaults.run_budget_minutes)
    parser.add_argument("--short-retention", type=int, default=defaults.short_retention_days)
    parser.add_argument("--long-retention", type=int, default=defaults.long_retention_days)
    parser.add_argument(
        "--heavy", default=",".join(defaults.heavy_paths), help="comma-separated heavy path names"
    )
    parser.add_argument("--requirements", default=None, help="file holding the playwright== pin")
    args = parser.parse_args(argv)

    shape = (
        None
        if args.structure_only
        else ShapeOptions(
            run_budget_minutes=args.run_budget,
            short_retention_days=args.short_retention,
            long_retention_days=args.long_retention,
            heavy_paths=tuple(p.strip() for p in args.heavy.split(",") if p.strip()),
            requirements=Path(args.requirements) if args.requirements else None,
        )
    )
    files = [Path(f) for f in args.files] or sorted(
        [*Path(args.dir).glob("*.yml"), *Path(args.dir).glob("*.yaml")]
    )
    results = [parse_check(f, shape=shape) for f in files]
    if shape is not None and not args.files and files:
        scan = missing_secret_scan(args.dir) + shared_gate_concurrency(args.dir)
        if scan:
            results.append(CheckResult("failed", scan))
    if args.actionlint:
        present = [f for f in files if f.is_file()]
        if present:
            results.append(actionlint_check(present))
    for result in results:
        if result.detail:
            print(result.detail, file=out)
        for finding in result.findings:
            print(finding, file=out)
    worst = max((r.exit_code for r in results if r.status != "abstained"), default=EXIT_OK)
    if worst == EXIT_OK and any(r.status == "abstained" for r in results):
        return EXIT_ABSTAINED
    if worst == EXIT_OK:
        print(f"workflow lint: {len(files)} file(s) clean", file=out)
    return worst


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
