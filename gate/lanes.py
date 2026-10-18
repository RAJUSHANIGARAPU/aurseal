"""Execution policy as code: which markers the default gate excludes, which lanes are
opt-in, what runs first, and what must run sequentially.

The policy, and why:

* **Unit lane first, separately.** The framework's own offline tests run in
  their own invocation before anything touches an environment. A broken
  harness fails fast and cheaply, and the gate's report then describes
  product flows only, not framework internals.
* **Default-gate exclusions.** ``unit`` (it has its own lane) and every
  opt-in lane are excluded from the gate unless their toggle is set. Detector
  (expected-red) tests are deliberately *never* excluded: their verdicts are
  what the gate reports on.
* **Token-only lanes never gate.** ``render_check`` tests authenticate only with a
  supplied bearer token, so they run in their own job when asked for
  (``gate.token_lane``); in the gate they would only abstain.
* **Drafts never gate.** A generated test carries ``draft`` until a reviewed
  pull request removes the marker; it runs in its own non-required job, so it
  can neither redden nor green the gate (``traceability.review``).
* **Opt-in lanes.** ``on_demand`` (harness and configuration validations —
  not product signal) and ``slow`` run only when their variable is true. A
  test nobody has watched pass once must not be able to redden the gate.
* **Sequential by default for state-sharing tests.** Tests marked ``e2e`` or
  ``stateful`` drive a backend with no per-test isolation; run in parallel
  they race on shared records and cascade into failures that look like
  product defects. Their lane runs with ``-n 0`` unless the environment
  declares isolation (``E2E_PARALLEL_SAFE=true``).
* **An empty selection is a failure.** pytest exits 5 when nothing is
  selected; never mask it (``|| true``, ``|| [ $? -eq 5 ]``) — a marker typo
  produces exactly that state. ``gate.workflow_lint`` flags the masks.
* **Reruns are reported, not hidden.** At most one rerun in the gate lane; the
  run-trust marker merges a rerun into its run rather than replacing it.
* **A never-run test gets one watched run before it can gate.** A push to
  ``bringup/<test path>`` runs exactly that selection with every marker
  exclusion dropped, no reruns, and an abstention turned into a failure
  (``COVERAGE_VERDICT_REQUIRED``); see :func:`bringup_command`.
"""

from __future__ import annotations

import argparse
import glob
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from gate._env import env_bool


@dataclass(frozen=True)
class Lane:
    marker: str
    description: str
    opt_in_env: str | None = None  # set => excluded from the gate unless this is true
    excluded_from_gate: bool = False  # always excluded (it has its own lane)
    sequential: bool = False


LANES: tuple[Lane, ...] = (
    Lane("unit", "framework self-tests; own lane, run first", excluded_from_gate=True),
    Lane(
        "on_demand",
        "harness/config validations, not product signal; opt in with INCLUDE_MANUAL_CHECKS",
        opt_in_env="INCLUDE_MANUAL_CHECKS",
    ),
    Lane("slow", "long-running checks; opt in with RUN_SLOW_TESTS", opt_in_env="RUN_SLOW_TESTS"),
    Lane("e2e", "end-to-end against a shared backend; sequential", sequential=True),
    Lane("stateful", "mutates shared backend state; sequential", sequential=True),
    Lane(
        "draft",
        "generated test awaiting review; runs in the drafts job only, never gates",
        excluded_from_gate=True,
    ),
    Lane(
        "render_check",
        "document rendering behind a bearer token; own opt-in job (gate.token_lane), never gates",
        excluded_from_gate=True,
    ),
)

# Marker names the gate itself reads. Registered with the rest so --strict-markers holds.
GATE_MARKERS: dict[str, str] = {
    "seeded": "needs data it acquired; counted by the run-trust fixture-provenance rule",
    "journey_group(group)": "skip peers after a deterministic shared-step failure in the group",
}

PARALLEL_SAFE_ENV = "E2E_PARALLEL_SAFE"
GATE_RERUNS = 1


def lane(marker: str) -> Lane:
    for candidate in LANES:
        if candidate.marker == marker:
            return candidate
    raise KeyError(marker)


def excluded_markers(env: Mapping[str, str] | None = None) -> list[str]:
    """Markers the default gate excludes for this environment."""
    excluded = []
    for candidate in LANES:
        if candidate.excluded_from_gate:
            excluded.append(candidate.marker)
        elif candidate.opt_in_env and not env_bool(candidate.opt_in_env, False, env):
            excluded.append(candidate.marker)
    return excluded


def gate_marker_expression(base: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """``-m`` expression for the gate: ``base`` (if any) minus the excluded markers."""
    parts = [f"not {m}" for m in excluded_markers(env)]
    if base:
        parts.insert(0, f"({base})")
    return " and ".join(parts)


def needs_sequential(
    markers: list[str] | tuple[str, ...], env: Mapping[str, str] | None = None
) -> bool:
    if env_bool(PARALLEL_SAFE_ENV, False, env):
        return False
    return any(lane(m).sequential for m in markers if m in {x.marker for x in LANES})


def xdist_args(
    markers: list[str] | tuple[str, ...], env: Mapping[str, str] | None = None
) -> list[str]:
    return ["-n", "0"] if needs_sequential(markers, env) else ["-n", "auto"]


def unit_lane_command() -> list[str]:
    return ["pytest", "-m", "unit", "-n", "auto"]


def gate_command(
    base: str | None = None,
    markers: tuple[str, ...] = ("e2e",),
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """The gate invocation: exclusions, sequential when required, one reported rerun."""
    return [
        "pytest",
        "-m",
        gate_marker_expression(base, env),
        *xdist_args(markers, env),
        "--reruns",
        str(GATE_RERUNS),
    ]


LANE_ORDER: tuple[str, ...] = ("unit", "gate")


def register_markers(config: pytest.Config) -> None:
    for candidate in LANES:
        config.addinivalue_line("markers", f"{candidate.marker}: {candidate.description}")
    for name, description in GATE_MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {description}")


# ---------------------------------------------------------------------------
# bring-up: one watched run for a test that has never run
# ---------------------------------------------------------------------------

BRINGUP_PREFIX = "bringup/"
BRINGUP_SELECTION_ENV = "BRINGUP_SELECTION"
BRINGUP_REF_ENV = "GITHUB_REF_NAME"
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "reports"})


class BringupError(ValueError):
    """The branch or selection cannot name a test; never silently widened to "everything"."""


def bringup_node_id(ref: str) -> str:
    """Map a bring-up branch name to a pytest node id. Pure: no filesystem access.

    Git forbids ``:`` and ``*`` in branch names, so ``/`` stands in for ``::``
    after the first segment that ends in ``.py``::

        bringup/tests/gate/test_lanes.py                -> tests/gate/test_lanes.py
        bringup/tests/gate/test_lanes.py/TestX/test_y   -> tests/gate/test_lanes.py::TestX::test_y
        bringup/tests/gate                              -> tests/gate
        bringup/test_lanes                              -> test_lanes   (a module name)
    """
    name = ref.strip()
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/") :]
    if not name.startswith(BRINGUP_PREFIX):
        raise BringupError(f"{ref!r} is not a bring-up branch (expected {BRINGUP_PREFIX}<test>)")
    suffix = name[len(BRINGUP_PREFIX) :].strip("/")
    if not suffix:
        raise BringupError(f"{ref!r} names no test: an empty bring-up suffix selects nothing")
    parts = suffix.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BringupError(f"{ref!r} has an empty or relative path segment")
    for index, part in enumerate(parts):
        if part.endswith(".py"):
            path = "/".join(parts[: index + 1])
            return "::".join([path, *parts[index + 1 :]])
    return suffix


def _find_module(name: str, root: Path) -> list[Path]:
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if f"{name}.py" in filenames:
            hits.append(Path(dirpath, f"{name}.py").relative_to(root))
    return sorted(hits)


def resolve_selection(node_id: str, root: str | Path = ".") -> list[str]:
    """Turn one node id into pytest arguments, checking it names something real.

    * a path (optionally ``::``-qualified) must exist under ``root``;
    * a bare module name must match exactly one ``<name>.py`` under ``root``;
    * a glob in the path part is expanded and must match at least one file;
    * a trailing ``*`` on the last ``::`` part becomes ``-k <prefix>``. ``-k``
      matches substrings, so that selection can be wider than a prefix.
    """
    base = Path(root).resolve()
    path, _, rest = node_id.partition("::")
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise BringupError(f"{node_id!r} must be a path inside the repository")
    names = rest.split("::") if rest else []
    keyword: list[str] = []
    if names and names[-1].endswith("*"):
        prefix = names.pop().rstrip("*")
        if not prefix:
            raise BringupError(f"{node_id!r}: a bare '*' selects every test in the file")
        keyword = ["-k", prefix]
    if any(ch in path for ch in "*?["):
        files = sorted(glob.glob(path, root_dir=base))
        if not files:
            raise BringupError(f"{path!r} matches no file under {base}")
        if names:
            raise BringupError(f"{node_id!r}: a glob path cannot carry test names")
        return [*files, *keyword]
    if not (base / path).exists():
        if "/" in path or path.endswith(".py"):
            raise BringupError(f"{path!r} does not exist under {base}")
        hits = _find_module(path, base)
        if len(hits) != 1:
            found = ", ".join(map(str, hits)) or "none"
            raise BringupError(f"module {path!r} must match exactly one file, found: {found}")
        path = hits[0].as_posix()
    return ["::".join([path, *names]), *keyword]


def bringup_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The run's environment: every opt-in lane on, and an abstention fails."""
    result = dict(os.environ if env is None else env)
    for candidate in LANES:
        if candidate.opt_in_env:
            result[candidate.opt_in_env] = "true"
    result["COVERAGE_VERDICT_REQUIRED"] = "true"
    return result


def bringup_command(
    ref: str, root: str | Path = ".", env: Mapping[str, str] | None = None
) -> list[str]:
    """The bring-up invocation for a branch (or ``$BRINGUP_SELECTION`` when set).

    ``-m ""`` clears any marker expression from ``PYTEST_ADDOPTS``: the named
    test must not be filtered out by the gate's exclusions. No reruns: a
    bring-up run is watched once, and a retry would hide the flake it exists to
    show. Sequential, because the test's isolation is not yet known. Exit 5
    ("selected nothing") is pytest's own and is returned as is.
    """
    override = (os.environ if env is None else env).get(BRINGUP_SELECTION_ENV, "").strip()
    node_ids = override.split() if override else [bringup_node_id(ref)]
    selection: list[str] = []
    for node_id in node_ids:
        selection += resolve_selection(node_id, root)
    return ["pytest", *selection, "-m", "", "-n", "0"]


def run_bringup(
    ref: str,
    root: str | Path = ".",
    env: Mapping[str, str] | None = None,
    runner=subprocess.run,
) -> int:
    command = bringup_command(ref, root, env)
    print("bring-up:", shlex.join(command), flush=True)
    completed = runner(
        [sys.executable, "-m", *command], cwd=str(root), env=bringup_env(env), check=False
    )
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gate.lanes")
    sub = parser.add_subparsers(dest="command", required=True)
    bringup = sub.add_parser("bringup", help="run the test a bringup/<test> branch names")
    bringup.add_argument("--ref", default=os.environ.get(BRINGUP_REF_ENV, ""))
    bringup.add_argument("--root", default=".")
    bringup.add_argument("--dry-run", action="store_true", help="print the command only")
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            print(shlex.join(bringup_command(args.ref, args.root)))
            return 0
        return run_bringup(args.ref, args.root)
    except BringupError as exc:
        print(f"bring-up: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
