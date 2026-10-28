"""Manual-run inputs -> one validated pytest command line and environment for the gate lane.

A manually started run (``workflow_dispatch``) carries typed inputs: environment, preset,
auth mode, opt-in lanes, a one-off selection, seed mode... Each one is validated here
before any test starts, and an input that would be silently ignored is an error, not a
no-op. Scheduled and push runs get the defaults, and the defaults produce exactly the
literal gate command in ``.github/workflows/tests.yml`` (a test pins it), so the CI
selection gate keeps auditing a literal command.

The workflow passes each input as ``INPUT_<NAME>`` (``INPUT_ENV``, ``INPUT_PRESET``, ...);
locally the same names are CLI flags (``--env staging --preset smoke``).

* ``python -m gate.run_inputs resolve`` validates, prints the final command (token
  masked), appends it to the step summary, exports the environment to ``$GITHUB_ENV`` and
  writes ``custom=true|false`` to ``$GITHUB_OUTPUT`` (``false`` = the literal step runs).
* ``python -m gate.run_inputs run [--dry-run] [--shard K/N]`` validates and runs the
  command; ``--shard`` runs one shard of a sharded run (``-p gate.pytest_shard``) with that
  shard's own environment (:func:`shard_env`).
* ``python -m gate.run_inputs plan-shards`` collects the selection once and writes the
  shard plan (``gate.shard``) to the step summary, and ``indices=[1, ..., N]`` for the
  matrix to ``$GITHUB_OUTPUT``.
* ``python -m gate.run_inputs post-deploy`` validates an externally triggered post-deploy
  run (:func:`post_deploy_plan`) and exports its command, environment and description.

Exit 0 ok, 2 an input is invalid (nothing was started).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gate import lanes as gate_lanes
from gate._env import env_bool

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS_FILE = ROOT / "config" / "environments.json"

GATE_BASE = "smoke or api or contract"
GATE_EXECUTION = ("-n", "auto", "--browser-channel=chrome", "--junitxml=reports/junit-browser.xml")
PRESETS = ("all", "smoke", "regression")
AUTH_MODES = ("configured", "none", "ui", "password", "token")
SEED_MODES = ("fresh", "reuse", "targets")
# Workflow lanes that switch jobs/steps on (read by the workflow's `if:`s).
WORKFLOW_LANES = ("regression", "checks", "browser-matrix")
_TARGETS_RE = re.compile(r"[A-Za-z0-9._-]+(,[A-Za-z0-9._-]+)*")
SHARD_CHOICES = ("1", "2", "3", "4")
SHARD_DIR = "reports/shards"
BRINGUP_REF_PREFIX = "refs/heads/bringup/"
RELEASE_CANDIDATE_ENV = "RELEASE_CANDIDATE"
# Per-shard isolation the shard's tests read (the data prefix and the user pool are wired
# by the parallel-run work; this module only sets the variables).
AUTH_CACHE_ENV = "TEST_AUTH_CACHE_DIR"
DATA_PREFIX_ENV = "TEST_WORKER_PREFIX"
MIN_EXECUTED_ENV = "RUN_TRUST_MIN_EXECUTED"
DEFAULT_MIN_EXECUTED = 10


@dataclass(frozen=True)
class Input:
    name: str
    kind: str  # "string" | "boolean" | "choice"
    default: str
    consumer: str  # "command" (this module) or "workflow" (a job/step `if:`)


INPUTS: tuple[Input, ...] = (
    Input("env", "choice", "qa", "command"),
    Input("preset", "choice", "all", "command"),
    Input("auth_mode", "choice", "configured", "command"),
    Input("headless", "boolean", "true", "command"),
    Input("step_screenshots", "boolean", "false", "command"),
    Input("lanes", "string", "", "command"),
    Input("selection", "string", "", "command"),
    Input("keep_all_evidence", "boolean", "false", "command"),
    Input("strict_oracles", "boolean", "false", "command"),
    Input("require_config_binding", "boolean", "false", "command"),
    Input("token", "string", "", "command"),
    Input("seed_mode", "choice", "fresh", "command"),
    Input("entity_allowlist", "string", "", "command"),
    Input("browser_matrix", "boolean", "false", "workflow"),
    Input("run_token_lane", "boolean", "false", "workflow"),
    # Read by gate.service_selection (the gate step's opt-in narrowing), not by the command.
    Input("changed_services", "string", "", "workflow"),
    Input("shards", "choice", "1", "workflow"),
    Input("shard_users", "string", "", "command"),
)
INPUT_NAMES = frozenset(i.name for i in INPUTS)


class RunInputsError(ValueError):
    """An input is invalid or would be ignored; the message names the input."""


@dataclass(frozen=True)
class Plan:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    shards: int = 1
    shard_users: tuple[str, ...] = ()

    def printable(self) -> str:
        return shlex.join(self.argv)

    def is_default(self) -> bool:
        """The literal workflow step can run it (the environment reaches it via $GITHUB_ENV)."""
        return self.argv == default_command()


def env_var(name: str) -> str:
    return f"INPUT_{name.upper()}"


def declared_environments(path: str | Path = ENVIRONMENTS_FILE) -> list[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return sorted(k for k, v in data.items() if isinstance(v, dict))


def known_lanes() -> list[str]:
    opt_in = [lane.marker for lane in gate_lanes.LANES if lane.opt_in_env]
    return sorted({*opt_in, *WORKFLOW_LANES})


def default_command() -> list[str]:
    """The gate lane's command today; the literal workflow step is pinned to this."""
    return ["pytest", "-m", gate_lanes.gate_marker_expression(GATE_BASE, {}), *GATE_EXECUTION]


def read_inputs(environ: Mapping[str, str]) -> dict[str, str]:
    """Every input from ``INPUT_<NAME>``; blank means the default."""
    values = {}
    for item in INPUTS:
        raw = (environ.get(env_var(item.name)) or "").strip()
        values[item.name] = raw if raw else item.default
    return values


def _bool(name: str, raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no", ""):
        return False
    raise RunInputsError(f"input {name}={raw!r} is not a boolean")


def _choice(name: str, raw: str, choices: Sequence[str]) -> str:
    if raw not in choices:
        raise RunInputsError(f"input {name}={raw!r} is not one of {list(choices)}")
    return raw


def plan(
    values: Mapping[str, str],
    environments: Sequence[str] | None = None,
    root: str | Path = ROOT,
    context: Mapping[str, str] | None = None,
) -> Plan:
    """Validate every input, then build the command and environment. Raises RunInputsError.

    ``context`` is the run's own environment (``GITHUB_REF``, ``RELEASE_CANDIDATE``): what
    a sharded run must not be combined with is partly decided by how the run started.
    """
    context = context or {}
    values = {**{i.name: i.default for i in INPUTS}, **values}
    unknown = sorted(set(values) - INPUT_NAMES)
    if unknown:
        raise RunInputsError(f"unknown input(s) {unknown}")
    environments = list(environments) if environments is not None else declared_environments()
    env_name = values["env"]
    if env_name not in environments:
        raise RunInputsError(f"input env={env_name!r} is not declared; valid: {environments}")
    preset = _choice("preset", values["preset"], PRESETS)
    auth_mode = _choice("auth_mode", values["auth_mode"], AUTH_MODES)
    seed_mode = _choice("seed_mode", values["seed_mode"], SEED_MODES)
    headless = _bool("headless", values["headless"])
    flags = {
        name: _bool(name, values[name])
        for name in (
            "step_screenshots",
            "keep_all_evidence",
            "strict_oracles",
            "require_config_binding",
            "browser_matrix",
            "run_token_lane",
        )
    }

    lanes = [p.strip() for p in values["lanes"].split(",") if p.strip()]
    bad = sorted(set(lanes) - set(known_lanes()))
    if bad:
        raise RunInputsError(f"input lanes: unknown lane(s) {bad}; valid: {known_lanes()}")

    token = values["token"].strip()
    if token and auth_mode != "token":
        raise RunInputsError(
            f"input token is set but auth_mode={auth_mode!r}: the token would be ignored"
        )
    if auth_mode == "token" and not token:
        raise RunInputsError("input auth_mode='token' needs input token")

    targets = re.sub(r"\s+", "", values["entity_allowlist"])
    if seed_mode == "targets" and not targets:
        raise RunInputsError("input seed_mode='targets' needs input entity_allowlist")
    if targets and seed_mode != "targets":
        raise RunInputsError(
            f"input entity_allowlist is set but seed_mode={seed_mode!r}: the ids would be ignored"
        )
    if targets and not _TARGETS_RE.fullmatch(targets):
        raise RunInputsError(
            f"input entity_allowlist={targets!r} must be comma-separated ids ([A-Za-z0-9._-])"
        )

    services = values["changed_services"].replace(",", " ").split()
    if services and not _TARGETS_RE.fullmatch(",".join(services)):
        raise RunInputsError(
            f"input changed_services={values['changed_services']!r} must be service names "
            "([A-Za-z0-9._-]), comma or space separated"
        )

    env: dict[str, str] = {"TEST_ENV": env_name}
    notices: list[str] = []
    toggles: dict[str, str] = {}
    for lane in gate_lanes.LANES:
        if lane.opt_in_env and lane.marker in lanes:
            toggles[lane.opt_in_env] = "true"
    env.update(toggles)
    if auth_mode != "configured":
        env["AUTH_MODE"] = auth_mode
    if token:
        env["SUPPLIED_TOKEN"] = token
    if not headless:
        env["HEADLESS"] = "false"
    if flags["step_screenshots"]:
        env["ALLURE_STEP_SCREENSHOTS"] = "true"
    if flags["keep_all_evidence"]:
        env["TRACE_ALWAYS"] = "true"
        env["VIDEO_ALWAYS"] = "true"
    if flags["strict_oracles"]:
        env["STRICT_ORACLES"] = "true"
    if flags["require_config_binding"]:
        env["EXT_CONFIG_REQUIRED"] = "true"
    if seed_mode != "fresh":
        env["SEED_MODE"] = seed_mode
    if targets:
        env["ENTITY_ALLOWLIST"] = targets

    shards = int(_choice("shards", values["shards"], SHARD_CHOICES))
    users = tuple(u.strip() for u in values["shard_users"].split(",") if u.strip())
    _check_shards(shards, users, seed_mode, values["selection"], context)

    selection = values["selection"].split()
    if selection:
        if preset != "all":
            notices.append(
                f"preset {preset!r} ignored: a selection was given and it is the more specific "
                "intent"
            )
        args: list[str] = []
        try:
            for node_id in selection:
                args += gate_lanes.resolve_selection(node_id, root)
        except gate_lanes.BringupError as exc:
            raise RunInputsError(f"input selection: {exc}") from None
        for lane in gate_lanes.LANES:
            if lane.opt_in_env:
                env[lane.opt_in_env] = "true"
        env["COVERAGE_VERDICT_REQUIRED"] = "true"
        argv = ["pytest", *args, "-m", "", "-n", "0", *GATE_EXECUTION[2:]]
    else:
        base = GATE_BASE if preset == "all" else preset
        argv = [
            "pytest",
            "-m",
            gate_lanes.gate_marker_expression(base, toggles),
            *GATE_EXECUTION,
        ]
    return Plan(argv, env, notices, shards, users)


def _check_shards(
    shards: int,
    users: tuple[str, ...],
    seed_mode: str,
    selection: str,
    context: Mapping[str, str],
) -> None:
    """A sharded run refuses what would make its shards share or skip state."""
    if shards == 1:
        if users:
            raise RunInputsError(
                "input shard_users is set but shards=1: one login per shard needs shards > 1"
            )
        return
    if seed_mode != "fresh":
        raise RunInputsError(
            f"input shards={shards} needs seed_mode='fresh', not {seed_mode!r}: shards "
            "would share the reused or targeted entities"
        )
    if selection.strip():
        raise RunInputsError(
            f"input shards={shards} with a selection: a one-off selection is not sharded"
        )
    if (context.get("GITHUB_REF") or "").startswith(BRINGUP_REF_PREFIX):
        raise RunInputsError(f"input shards={shards} on a bring-up ref: one watched run only")
    if (context.get(RELEASE_CANDIDATE_ENV) or "").strip():
        raise RunInputsError(
            f"input shards={shards} with a release candidate: the verdict needs one unsharded "
            "run while the sharded run is a pilot"
        )
    if users and len(users) != shards:
        raise RunInputsError(
            f"input shard_users names {len(users)} login(s) for {shards} shards: one per shard"
        )


def shard_env(
    plan_: Plan, index: int, count: int, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment of shard ``index`` of ``count``: own auth cache, data prefix, login
    and an executed floor of ``ceil(min / count)`` (the merged run is held to the full one).
    """
    environ = os.environ if environ is None else environ
    if count != plan_.shards:
        raise RunInputsError(f"--shard {index}/{count} does not match input shards={plan_.shards}")
    raw_min = (environ.get(MIN_EXECUTED_ENV) or "").strip()
    try:
        minimum = int(raw_min) if raw_min else DEFAULT_MIN_EXECUTED
    except ValueError:
        minimum = DEFAULT_MIN_EXECUTED
    env = {
        AUTH_CACHE_ENV: f"reports/auth-cache/w{index}",
        DATA_PREFIX_ENV: f"w{index}",
        MIN_EXECUTED_ENV: str(math.ceil(minimum / count)) if minimum > 0 else str(minimum),
    }
    if plan_.shard_users:
        env["LOGIN_USERNAME"] = plan_.shard_users[index - 1]
    return env


def shard_argv(plan_: Plan, index: int, count: int) -> list[str]:
    """The plan's command for one shard; its JUnit file is named after the shard."""
    argv = [
        a if not a.startswith("--junitxml=") else f"--junitxml=reports/junit-shard-{index}.xml"
        for a in plan_.argv
    ]
    return [
        *argv,
        "-p",
        "gate.pytest_shard",
        f"--shard={index}/{count}",
        f"--shard-dir={SHARD_DIR}",
    ]


# ---------------------------------------------------------------- post-deploy runs

POST_DEPLOY_EVENTS = ("repository_dispatch", "workflow_dispatch")
POST_DEPLOY_PRESETS = ("smoke", "regression", "all")
# Environments the merge gate (tests.yml) also runs against: a post-deploy run there may
# only read, so a deploy never races the gate on shared data.
GATE_ENVIRONMENTS = frozenset({"qa"})
_SAFE_LABEL = re.compile(r"[A-Za-z0-9._/@:+-]{1,100}")


@dataclass(frozen=True)
class PostDeploy:
    plan: Plan
    source: str
    ref: str

    @property
    def description(self) -> str:
        env = self.plan.env.get("TEST_ENV", "?")
        ref = f" @ {self.ref}" if self.ref else ""
        return f"post-deploy {env} | triggered by {self.source}{ref}"


def post_deploy_plan(
    event: str,
    payload: Mapping[str, str],
    environments: Sequence[str] | None = None,
) -> PostDeploy:
    """Validate a post-deploy trigger. Raises RunInputsError; never a silent green."""
    if event not in POST_DEPLOY_EVENTS:
        raise RunInputsError(
            f"event {event!r} cannot start a post-deploy run; only {list(POST_DEPLOY_EVENTS)}"
        )
    unknown = sorted(set(payload) - {"env", "preset", "source", "ref"})
    if unknown:
        raise RunInputsError(f"unknown post-deploy field(s) {unknown}")
    environments = list(environments) if environments is not None else declared_environments()
    env_name = (payload.get("env") or "").strip()
    if not env_name:
        raise RunInputsError("a post-deploy run needs env")
    if env_name not in environments:
        raise RunInputsError(f"post-deploy env={env_name!r} is not declared; valid: {environments}")
    preset = (payload.get("preset") or "smoke").strip()
    _choice("preset", preset, POST_DEPLOY_PRESETS)
    if env_name in GATE_ENVIRONMENTS and preset != "smoke":
        raise RunInputsError(
            f"post-deploy env={env_name!r} is shared with the merge gate: only the read-only "
            f"'smoke' preset may run there, not {preset!r}"
        )
    source = (payload.get("source") or "").strip()
    if not source:
        if event != "workflow_dispatch":
            raise RunInputsError("a repository_dispatch post-deploy run needs source")
        source = "manual"
    ref = (payload.get("ref") or "").strip()
    for name, value in (("source", source), ("ref", ref)):
        if value and not _SAFE_LABEL.fullmatch(value):
            raise RunInputsError(f"post-deploy {name}={value!r} has characters outside a label")
    base = plan({"env": env_name, "preset": preset}, environments)
    argv = [*base.argv, "--reruns", "0"]
    argv = [
        "--junitxml=reports/junit-post-deploy.xml" if a.startswith("--junitxml=") else a
        for a in argv
    ]
    return PostDeploy(Plan(argv, base.env, base.notices), source, ref)


def masked(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


def _write_env(path: str, env: Mapping[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in env.items():
            delimiter = f"EOF_{secrets.token_hex(8)}"
            handle.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


Spawn = Callable[[list[str], dict[str, str]], int]


def _spawn(argv: list[str], env: dict[str, str]) -> int:
    return subprocess.run([sys.executable, "-m", *argv], env=env, check=False).returncode


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


Collect = Callable[[list[str]], str]


def _collect(argv: list[str]) -> str:
    """``pytest --collect-only -q`` over a plan's selection; returns its stdout."""
    selecting = [a for a in argv[1:] if not a.startswith("--junitxml=")]
    done = subprocess.run(
        [sys.executable, "-m", "pytest", *selecting, "--collect-only", "-q", "-n", "0"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout


def _plan_shards(result: Plan, args, collect: Collect, out) -> int:
    from gate import shard as gate_shard

    if result.shards < 2:
        print("plan-shards: shards=1, nothing to plan", file=out)
        _append(args.github_output, "indices=[1]\n")
        return 0
    nodeids = [line.strip() for line in collect(result.argv).splitlines() if "::" in line]
    if not nodeids:
        print("plan-shards: the selection collected no tests", file=sys.stderr)
        return 2
    durations = gate_shard.load_durations(os.environ.get(gate_shard.DURATIONS_ENV))
    planned = gate_shard.plan_shards(nodeids, durations, result.shards)
    loads = gate_shard.plan_load(planned, durations)
    lines = [f"Shard plan: {len(nodeids)} tests over {result.shards} shards"]
    lines += [
        f"- shard {k}/{result.shards}: {len(members)} tests, ~{load}s"
        for k, (members, load) in enumerate(zip(planned, loads, strict=True), start=1)
    ]
    print("\n".join(lines), file=out)
    _append(args.summary, "\n".join(lines) + "\n")
    _append(args.github_output, f"indices={json.dumps(list(range(1, result.shards + 1)))}\n")
    return 0


def _post_deploy(args, environ: Mapping[str, str], spawn: Spawn, out) -> int:
    payload = {
        name: (environ.get(env_var(name)) or "").strip()
        for name in ("env", "preset", "source", "ref")
    }
    try:
        run = post_deploy_plan(environ.get("GITHUB_EVENT_NAME", ""), payload)
    except RunInputsError as exc:
        print(f"post-deploy: {exc}", file=sys.stderr)
        return 2
    print(f"command: {run.plan.printable()}", file=out)
    print(f"description: {run.description}", file=out)
    _append(args.summary, f"Post-deploy run: {run.description}\n\n`{run.plan.printable()}`\n")
    _append(args.github_output, f"description={run.description}\n")
    if args.github_env:
        _write_env(args.github_env, run.plan.env)
    if args.dry_run or not args.execute:
        return 0
    child = {**environ, **run.plan.env, "PYTHONUNBUFFERED": "1"}
    return spawn(run.plan.argv, child)


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    spawn: Spawn = _spawn,
    out=None,
    collect: Collect = _collect,
) -> int:
    environ = dict(os.environ if environ is None else environ)
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m gate.run_inputs")
    parser.add_argument("command", choices=("resolve", "run", "plan-shards", "post-deploy"))
    parser.add_argument("--dry-run", action="store_true", help="print, do not run")
    parser.add_argument("--shard", default=None, help="run: one shard of a sharded run (K/N)")
    parser.add_argument(
        "--execute", action="store_true", help="post-deploy: run the validated command"
    )
    parser.add_argument("--github-env", default=environ.get("GITHUB_ENV"))
    parser.add_argument("--github-output", default=environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--summary", default=environ.get("GITHUB_STEP_SUMMARY"))
    for item in INPUTS:
        parser.add_argument(f"--{item.name.replace('_', '-')}", dest=item.name, default=None)
    args = parser.parse_args(argv)

    if args.command == "post-deploy":
        return _post_deploy(args, environ, spawn, out)

    values = read_inputs(environ)
    for item in INPUTS:
        given = getattr(args, item.name)
        if given is not None:
            values[item.name] = given
    try:
        result = plan(values, context=environ)
        shard_spec = None
        if args.shard:
            from gate.shard import parse_shard

            try:
                shard_spec = parse_shard(args.shard)
            except ValueError as exc:
                raise RunInputsError(str(exc)) from None
            extra = shard_env(result, shard_spec.index, shard_spec.count, environ)
            result = Plan(
                shard_argv(result, shard_spec.index, shard_spec.count),
                {**result.env, **extra},
                result.notices,
                result.shards,
                result.shard_users,
            )
    except RunInputsError as exc:
        print(f"run inputs: {exc}", file=sys.stderr)
        return 2
    if args.command == "plan-shards":
        return _plan_shards(result, args, collect, out)
    token = result.env.get("SUPPLIED_TOKEN")
    for notice in result.notices:
        print(f"run inputs: {notice}", file=out)
    shown = masked(result.printable(), token)
    exported = {k: ("***" if k == "SUPPLIED_TOKEN" else v) for k, v in result.env.items()}
    print(f"command: {shown}", file=out)
    print(f"environment: {json.dumps(exported, sort_keys=True)}", file=out)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(f"Gate command: `{shown}`\n")
            for notice in result.notices:
                handle.write(f"- {notice}\n")
    if args.command == "resolve":
        if args.github_env:
            _write_env(args.github_env, result.env)
        if args.github_output:
            with open(args.github_output, "a", encoding="utf-8") as handle:
                handle.write(f"custom={'false' if result.is_default() else 'true'}\n")
        return 0
    if args.dry_run:
        return 0
    child = {**environ, **result.env}
    if env_bool("CI", False, environ):
        child.setdefault("PYTHONUNBUFFERED", "1")
    return spawn(result.argv, child)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
