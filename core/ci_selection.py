"""
Find tests that exist in the repository but that no CI job ever selects.

A test suite has two failure modes. The loud one is a test that fails. The
quiet one is a test that never runs — and from outside the two are not
distinguishable, because both produce a green build.

This repository had the quiet one. It collected 523 tests; CI ran ``-m smoke``
(2) and ``-m regression`` (5). The other 516 executed on nobody's machine but
the author's, and 515 of them pass offline in 22 seconds, so there was never a
cost reason for the gap. It was simply invisible: marker-based selection tells
you what it *included* and stays silent about the rest.

The check below closes that. It reads the workflow file, replays each pytest
invocation it finds under ``--collect-only``, and compares the union of what
those invocations select against the full collection. Anything left over is a
test the CI badge does not stand behind.

Two design choices are deliberate:

*Selection is delegated to pytest, never reimplemented.* Marker expressions,
``-k`` filters, ``--ignore``, ``testpaths`` and conftest-level collection hooks
all decide what runs. Re-deriving that here would mean writing a second,
subtly different selector and then trusting it to audit the first — the same
mistake as a stub shaped around the implementation instead of the real thing.
So each invocation is handed back to pytest verbatim and pytest is asked what
it would have picked.

*Unrecognised arguments are passed through, not dropped.* Only flags known to
affect execution rather than selection are stripped. If a future flag does
change selection and this module has not heard of it, it reaches pytest and
either works or fails loudly. The alternative — a whitelist — would silently
under-report, which is the exact failure this module exists to detect.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gate.browsers import strip_browser_param

# Flags that change how a run executes but not which tests it selects.
# `-n auto` distributes across workers; `--reruns` retries failures; the
# reporting flags only decide what gets written. None of them alter the set of
# collected node ids, and several are meaningless under --collect-only.
_EXECUTION_ONLY_FLAGS: frozenset[str] = frozenset(
    {"-v", "-vv", "-q", "-s", "-x", "--tb", "--exitfirst", "--headed"}
)

# Same, but these consume a following value (`-n auto`, `--reruns 2`).
# The shard options split one selection across machines: the union of the
# shards is the unsharded selection, which is what the gate must replay.
_EXECUTION_ONLY_FLAGS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-n",
        "--numprocesses",
        "--reruns",
        "--reruns-delay",
        "--alluredir",
        "--tb",
        "--shard",
        "--shard-durations",
        "--shard-dir",
        # Which engine a browser test runs on. A matrix leg selects the same tests as
        # the default engine; its node ids differ only in the engine parameter.
        "--browser",
        "--browser-channel",
    }
)

# Plugins that only change how the selection is executed. `-p <name>` naming one
# of these is dropped together with its options; any other `-p` is kept, since a
# plugin can change collection.
_EXECUTION_ONLY_PLUGINS: frozenset[str] = frozenset({"gate.pytest_shard"})

# pytest exits 5 when a selection matches nothing. That is a legitimate answer
# to "what does this invocation select?" — the answer is "nothing" — and it is
# also precisely the state a marker typo produces, so it must not be swallowed
# as an error here; it shows up instead as an uncovered set.
_EXIT_OK = (0, 5)

# `--collect-only` emits one node id per line at exactly verbosity -1. At 0 it
# prints an indented tree; at -2 and below it collapses to per-file counts.
# Neither yields node ids.
#
# That window is narrow and this project's pytest.ini sets `addopts = -v`, which
# is prepended to the command line, so a lone `-q` lands back on the tree — how
# the first version of this module parsed zero ids out of a successful
# collection and concluded CI covered everything. Piling on more `-q` overshoots
# into the other unusable format.
#
# So verbosity is set rather than nudged: clear the ini addopts, then apply one
# `-q`. Nothing currently in addopts affects which tests are chosen, and if
# something ever did, clearing it widens the baseline — the gate would name
# tests as unrun that CI does in fact skip. That is the safe direction to be
# wrong in: it fails loudly and someone looks, where the opposite failure is
# silent and reports full coverage.
_DETERMINISTIC_VERBOSITY = ("-o", "addopts=", "-q")

# "523 tests collected", "2/523 tests collected (521 deselected)",
# "collected 554 items". The first number is always what was selected.
_COLLECTED_RE = re.compile(
    r"(?:^|\s)(\d+)(?:/\d+)?\s+(?:tests?|items?)\s+collected|collected\s+(\d+)\s+items?"
)


@dataclass(frozen=True)
class PytestInvocation:
    """A single ``pytest ...`` command found in a CI workflow."""

    job: str
    step: str
    args: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        return f"{self.job} / {self.step}: pytest {' '.join(self.args)}".rstrip()


def _iter_run_steps(workflow: dict):
    """Yield (job, step name, shell body) for every ``run:`` step."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            body = step.get("run")
            if body:
                yield job_name, step.get("name") or "<unnamed step>", str(body)


def parse_pytest_invocations(workflow_path: str | Path) -> list[PytestInvocation]:
    """
    Extract every pytest command a workflow runs.

    Recognises both ``pytest ...`` and ``python -m pytest ...``. A line that
    mentions pytest but cannot be tokenised raises rather than being skipped:
    an unparseable invocation is an unknown quantity, and treating it as absent
    would overstate the gap while treating it as total coverage would hide one.
    """
    data = yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8")) or {}
    invocations: list[PytestInvocation] = []

    for job, step, body in _iter_run_steps(data):
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                tokens = shlex.split(line)
            except ValueError as exc:
                if "pytest" in line:
                    raise ValueError(
                        f"cannot parse a pytest command in {job} / {step}: {line!r} ({exc})"
                    ) from exc
                continue

            if not tokens:
                continue
            if tokens[0] == "pytest":
                args = tokens[1:]
            elif tokens[:3] == ["python", "-m", "pytest"]:
                args = tokens[3:]
            else:
                continue

            invocations.append(PytestInvocation(job=job, step=step, args=tuple(args)))

    return invocations


def strip_execution_only_flags(args: tuple[str, ...] | list[str]) -> list[str]:
    """Drop flags that affect how tests run but not which tests are chosen."""
    kept: list[str] = []
    skip_next = False

    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue

        if arg == "-p" and index + 1 < len(args) and args[index + 1] in _EXECUTION_ONLY_PLUGINS:
            skip_next = True
            continue
        if arg.startswith("-p") and arg[2:].lstrip("=") in _EXECUTION_ONLY_PLUGINS:
            continue

        name = arg.split("=", 1)[0]
        if name in _EXECUTION_ONLY_FLAGS:
            continue
        if name in _EXECUTION_ONLY_FLAGS_WITH_VALUE:
            skip_next = "=" not in arg
            continue
        kept.append(arg)

    return kept


def collect_node_ids(args: tuple[str, ...] | list[str], rootdir: str | Path) -> set[str]:
    """
    Ask pytest which tests a given argument list selects.

    Runs in a subprocess so the calling test session's own state — registered
    plugins, already-imported conftests, the current ``-m`` expression — cannot
    influence the answer.
    """
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        *_DETERMINISTIC_VERBOSITY,
        "-p",
        "no:cacheprovider",
        *strip_execution_only_flags(args),
    ]
    result = subprocess.run(
        command,
        cwd=str(rootdir),
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode not in _EXIT_OK:
        raise RuntimeError(
            "collection failed for: pytest "
            + " ".join(args)
            + f"\nexit={result.returncode}\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )

    node_ids = parse_node_ids(result.stdout)
    reported = reported_count(result.stdout)

    # Self-check. Everything downstream treats an empty result as "this command
    # selects nothing", which is indistinguishable from "the output format
    # changed and the parser understood none of it" — and the second reads as
    # full coverage. Cross-checking against pytest's own tally turns a silent
    # wrong answer into a loud one.
    if reported is not None and reported != len(node_ids):
        raise RuntimeError(
            f"parsed {len(node_ids)} node ids but pytest reported {reported} collected, "
            f"for: pytest {' '.join(args)}\n"
            "The --collect-only output format is not what this parser expects.\n"
            f"{result.stdout[:2000]}"
        )

    return node_ids


def reported_count(collect_output: str) -> int | None:
    """The number of tests pytest says it collected, or None if it did not say."""
    match = _COLLECTED_RE.search(collect_output)
    if match is None:
        return 0 if "no tests ran" in collect_output else None
    return int(match.group(1) or match.group(2))


def parse_node_ids(collect_output: str) -> set[str]:
    """
    Pull node ids out of ``pytest --collect-only -q`` output.

    A node id is ``<path>.py::<something>``; the trailing summary lines and any
    warnings are not. Parametrised ids may contain spaces (``test_x[a b]``), so
    the shape of the prefix is what identifies a line, not whitespace.
    """
    node_ids: set[str] = set()
    for line in collect_output.splitlines():
        candidate = line.strip()
        if "::" not in candidate:
            continue
        head = candidate.split("::", 1)[0]
        if head.endswith(".py"):
            node_ids.add(candidate)
    return node_ids


@dataclass(frozen=True)
class SelectionReport:
    """What CI covers, what it misses, and which commands were considered."""

    all_tests: frozenset[str]
    covered: frozenset[str]
    invocations: tuple[PytestInvocation, ...]

    @property
    def unrun(self) -> frozenset[str]:
        return frozenset(self.all_tests - self.covered)

    @property
    def coverage_ratio(self) -> float:
        if not self.all_tests:
            return 1.0
        return len(self.covered) / len(self.all_tests)

    def describe(self, limit: int = 25) -> str:
        missing = sorted(self.unrun)
        lines = [
            f"{len(missing)} of {len(self.all_tests)} collected tests are selected "
            f"by no CI job ({self.coverage_ratio:.0%} covered).",
            "",
            "pytest commands found in the workflow:",
        ]
        lines += [f"  - {inv}" for inv in self.invocations] or ["  (none)"]
        lines += ["", "Tests no CI job runs:"]
        lines += [f"  - {node}" for node in missing[:limit]]
        if len(missing) > limit:
            lines.append(f"  ... and {len(missing) - limit} more")
        return "\n".join(lines)


def match_covered(everything: set[str], selected: set[str]) -> set[str]:
    """The collected ids a selection covers, treating engine variants as one test.

    ``test_x[firefox]`` selected by a matrix leg covers ``test_x[chromium]`` in the
    default collection: same test, different engine.
    """
    direct = selected & everything
    by_identity = {strip_browser_param(nid) for nid in selected}
    return direct | {nid for nid in everything if strip_browser_param(nid) in by_identity}


def analyse(workflow_path: str | Path, rootdir: str | Path) -> SelectionReport:
    """Compare everything collectable against everything CI selects."""
    everything = collect_node_ids([], rootdir)
    invocations = parse_pytest_invocations(workflow_path)

    covered: set[str] = set()
    for invocation in invocations:
        covered |= collect_node_ids(invocation.args, rootdir)

    return SelectionReport(
        all_tests=frozenset(everything),
        # A workflow can name a path that no longer exists, or select a test
        # outside the current collection; intersecting keeps the ratio honest.
        covered=frozenset(match_covered(everything, covered)),
        invocations=tuple(invocations),
    )
