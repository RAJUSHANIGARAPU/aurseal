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
* ``python -m gate.run_inputs run [--dry-run]`` validates and runs the command.

Exit 0 ok, 2 an input is invalid (nothing was started).
"""

from __future__ import annotations

import argparse
import json
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
)
INPUT_NAMES = frozenset(i.name for i in INPUTS)


class RunInputsError(ValueError):
    """An input is invalid or would be ignored; the message names the input."""


@dataclass(frozen=True)
class Plan:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)

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
) -> Plan:
    """Validate every input, then build the command and environment. Raises RunInputsError."""
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
    return Plan(argv, env, notices)


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


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    spawn: Spawn = _spawn,
    out=None,
) -> int:
    environ = dict(os.environ if environ is None else environ)
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m gate.run_inputs")
    parser.add_argument("command", choices=("resolve", "run"))
    parser.add_argument("--dry-run", action="store_true", help="print, do not run")
    parser.add_argument("--github-env", default=environ.get("GITHUB_ENV"))
    parser.add_argument("--github-output", default=environ.get("GITHUB_OUTPUT"))
    parser.add_argument("--summary", default=environ.get("GITHUB_STEP_SUMMARY"))
    for item in INPUTS:
        parser.add_argument(f"--{item.name.replace('_', '-')}", dest=item.name, default=None)
    args = parser.parse_args(argv)

    values = read_inputs(environ)
    for item in INPUTS:
        given = getattr(args, item.name)
        if given is not None:
            values[item.name] = given
    try:
        result = plan(values)
    except RunInputsError as exc:
        print(f"run inputs: {exc}", file=sys.stderr)
        return 2
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
