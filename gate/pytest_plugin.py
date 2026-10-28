"""pytest wiring for the run gate.

Registered from the root ``conftest.py`` via :func:`register`; elsewhere use
``-p gate.pytest_plugin``. What it adds:

* the lane/gate marker taxonomy (``gate.lanes``);
* a run id shared by controller, workers and reruns (``gate.run_id``);
* the environment circuit breaker's abstention: once tripped, every later
  test skips before sending traffic (``gate.circuit_breaker``);
* journey-group fail-fast (``gate.journey_group``);
* ``@pytest.mark.only_env("qa", "staging")``: on any other environment the test skips
  as not applicable at setup (``gate.requirement.runs_only_on``), never as a failure;
* the run-trust gate, when ``RUN_TRUST`` is ``fail``, ``unstable`` or
  ``report`` (``gate.integrity``). Unset, the gate is off: a developer running
  one test is not held to a gate's bar.

xdist: the controller replays every worker's reports through
``pytest_runtest_logreport``, so the run-trust tally is built on the
controller only (outcomes, markers via ``report.keywords`` and expected-red
ids via ``user_properties`` all travel with the report). Workers only inherit
the run id. pytest-rerunfailures: an attempt that will be retried reports
``rerun`` and is discarded; the test's final attempt is its answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.logger import logger
from gate import integrity
from gate._env import env_list, env_str
from gate.circuit_breaker import ServerErrorBreaker, default_breaker, install_default
from gate.integrity import (
    EXECUTED,
    Enforcement,
    Outcome,
    RunTrustGate,
    TestResult,
    Thresholds,
    build_tally,
    earlier_invocation_results,
    write_markers,
)
from gate.journey_group import JourneyGroupPlugin
from gate.lanes import register_markers
from gate.provenance import read_rows
from gate.requirement import runs_only_on
from gate.run_id import WORKERINPUT_KEY, ensure_run_id
from gate.verdicts import read_verdict_rows

DETECTOR_MARKERS_ENV = "RUN_TRUST_DETECTOR_MARKERS"
SEEDED_MARKERS_ENV = "RUN_TRUST_SEEDED_MARKERS"
DEFECT_PROPERTY = "known_defect"  # user_properties key the expected-red marker writes

PLUGIN_NAME = "run-gate"
INTEGRITY_PLUGIN_NAME = "run-trust"
JOURNEY_GROUP_PLUGIN_NAME = "journey-group"
ONLY_ENV_MARKER = "only_env"


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        text = str(longrepr[2])
        return text[len("Skipped: ") :] if text.startswith("Skipped: ") else text
    return str(longrepr or "")[:500]


class _Running:
    """Mutable per-test state while reports arrive."""

    __slots__ = ("outcome", "markers", "reason", "defect", "cleared")

    def __init__(self, markers: tuple[str, ...]) -> None:
        self.outcome: Outcome | None = None
        self.markers = markers
        self.reason = ""
        self.defect: str | None = None
        self.cleared = False


class RunTrustPlugin:
    """Tallies the run on the controller and judges it at session end."""

    def __init__(
        self,
        gate: RunTrustGate,
        run_id: str,
        *,
        marker_dir: str | Path,
        lane: str = integrity.DEFAULT_LANE,
        detector_markers: tuple[str, ...] = ("known_defect",),
        seeded_markers: tuple[str, ...] = ("seeded",),
        verdicts_file: str | Path | None = None,
        provenance_file: str | Path | None = None,
    ) -> None:
        self.gate = gate
        self.run_id = run_id
        self.marker_dir = Path(marker_dir)
        self.lane = lane
        self.detector_markers = detector_markers
        self.seeded_markers = seeded_markers
        self.verdicts_file = verdicts_file
        self.provenance_file = provenance_file
        self._tracked = frozenset(detector_markers) | frozenset(seeded_markers)
        self._collected: list[str] = []
        self._running: dict[str, _Running] = {}
        self.summary_lines: list[str] = []
        self.result: integrity.Result | None = None

    @classmethod
    def from_env(cls, run_id: str) -> RunTrustPlugin:
        return cls(
            RunTrustGate(Thresholds.from_env()),
            run_id,
            marker_dir=env_str(integrity.DIR_ENV, str(integrity.DEFAULT_DIR)),
            lane=env_str(integrity.LANE_ENV, integrity.DEFAULT_LANE),
            detector_markers=env_list(DETECTOR_MARKERS_ENV, ("known_defect",)),
            seeded_markers=env_list(SEEDED_MARKERS_ENV, ("seeded",)),
        )

    # ------------------------------------------------------------- selection

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        # Not called with useful items on an xdist controller (it collects nothing);
        # the xdist hook below supplies the selection there.
        self._collected.extend(item.nodeid for item in session.items)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids: list[str]) -> None:
        if not self._collected:
            self._collected.extend(ids)

    # ------------------------------------------------------------- outcomes

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        state = self._running.get(report.nodeid)
        if state is None:
            markers = tuple(k for k in self._tracked if k in report.keywords)
            state = self._running[report.nodeid] = _Running(markers)
        for key, value in report.user_properties:
            if key == DEFECT_PROPERTY and isinstance(value, str):
                state.defect = value

        outcome = report.outcome
        if outcome == "rerun":
            # This attempt will be retried; only the final attempt answers.
            state.outcome, state.reason, state.cleared = None, "", False
            return
        if report.when == "setup":
            if outcome == "failed":
                state.outcome = Outcome.ERROR
            elif outcome == "skipped":
                state.outcome, state.reason = Outcome.SKIPPED, _skip_reason(report)
        elif report.when == "call":
            if outcome == "passed":
                state.outcome = Outcome.PASSED
            elif outcome == "failed":
                state.outcome = Outcome.FAILED
                state.cleared = str(report.longrepr or "").startswith("[XPASS(strict)]")
            elif hasattr(report, "wasxfail"):
                state.outcome = Outcome.XFAILED
            else:
                state.outcome, state.reason = Outcome.SKIPPED, _skip_reason(report)
        elif report.when == "teardown" and outcome == "failed":
            if state.outcome in (None, Outcome.PASSED):
                state.outcome = Outcome.ERROR

    def results(self) -> list[TestResult]:
        ordered = list(dict.fromkeys([*self._collected, *self._running]))
        out: list[TestResult] = []
        for nodeid in ordered:
            state = self._running.get(nodeid)
            if state is None or state.outcome is None:
                out.append(
                    TestResult(
                        nodeid,
                        Outcome.NOT_RUN,
                        state.markers if state else (),
                        "selected but never reported (interrupted or crashed?)",
                    )
                )
                continue
            out.append(
                TestResult(
                    nodeid, state.outcome, state.markers, state.reason, state.defect, state.cleared
                )
            )
        return out

    # ------------------------------------------------------------- verdict

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        current = {t.nodeid: t for t in self.results()}
        earlier = earlier_invocation_results(self.marker_dir, self.run_id, self.lane)
        if earlier:
            # Same run, later invocation (e.g. --lf): this attempt's answers replace the
            # earlier ones for the tests it re-ran; everything else stands.
            current = {**earlier, **current}
            logger.info(
                "[run-gate] merged %d test(s) from an earlier invocation of run %s",
                len(earlier),
                self.run_id,
            )
        tests = list(current.values())
        tally = build_tally(
            tests,
            read_verdict_rows(self.verdicts_file, run_id=self.run_id),
            read_rows(self.provenance_file, run_id=self.run_id),
            self.detector_markers,
            self.seeded_markers,
        )
        result = self.gate.evaluate(tally)
        self.result = result
        self.summary_lines = self.gate.summary(tally, result)
        write_markers(
            self.marker_dir,
            run_id=self.run_id,
            lane=self.lane,
            tests=tests,
            tally=tally,
            result=result,
            gate=self.gate,
        )
        log = logger.error if result.verdict is integrity.Verdict.FAIL else logger.info
        log("[run-gate] %s", result.headline)
        if result.fails_the_build and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if not self.summary_lines:
            return
        red = self.result is not None and self.result.verdict is integrity.Verdict.FAIL
        terminalreporter.section("run trust", red=red, green=not red)
        for line in self.summary_lines:
            terminalreporter.write_line(line)


class RunGatePlugin:
    """Run id propagation and circuit-breaker abstention."""

    def __init__(self, run_id: str, breaker: ServerErrorBreaker) -> None:
        self.run_id = run_id
        self.breaker = breaker

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node) -> None:
        node.workerinput[WORKERINPUT_KEY] = self.run_id

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        # Out of scope is decided first: a tripped breaker must not turn it into an abstention.
        marker = item.get_closest_marker(ONLY_ENV_MARKER)
        if marker is not None:
            runs_only_on(
                marker.args,
                f"{item.name}",
                item.config.getoption("--env", default=None),
                covered_by=marker.kwargs.get("covered_by"),
            )
        self.breaker.assume_not_tripped()

    @pytest.fixture
    def circuit_breaker(self) -> ServerErrorBreaker:
        """The run's breaker: record responses, or declare an expected 5xx."""
        return self.breaker


def _trip_file(run_id: str) -> Path:
    base = Path(env_str(integrity.DIR_ENV, str(integrity.DEFAULT_DIR)) or "reports")
    return (base / "circuit-breaker" / f"{run_id}.tripped").resolve()


def _configured_hosts(config: pytest.Config) -> tuple[str, ...]:
    """The environment's own base URL, so the breaker guards it without extra setup."""
    try:
        from core.config_reader import ConfigReader

        return (ConfigReader(config.getoption("--env", default="qa")).get_base_url(),)
    except Exception:
        return ()


def register(config: pytest.Config) -> None:
    register_markers(config)
    config.addinivalue_line(
        "markers",
        f"{ONLY_ENV_MARKER}(*envs, covered_by=None): runs only on these environments; "
        "elsewhere it skips as not applicable, even in a lane that requires verdicts",
    )
    if config.pluginmanager.hasplugin(PLUGIN_NAME):
        return
    run_id = ensure_run_id(config)
    breaker = default_breaker()
    if not breaker.enabled or breaker.trip_file is None:
        breaker = install_default(
            ServerErrorBreaker.from_env(
                trip_file=_trip_file(run_id), fallback_hosts=_configured_hosts(config)
            )
        )
    config.pluginmanager.register(RunGatePlugin(run_id, breaker), PLUGIN_NAME)
    config.pluginmanager.register(JourneyGroupPlugin(), JOURNEY_GROUP_PLUGIN_NAME)

    enforcement = Enforcement.parse(env_str(integrity.ENFORCEMENT_ENV, None))
    is_worker = hasattr(config, "workerinput")
    # A collect-only session ran nothing and claims nothing; judging it would turn every
    # `--collect-only` (including the CI-selection audit's) into a 100% skip ratio.
    collect_only = bool(getattr(config.option, "collectonly", False))
    if enforcement is not None and not is_worker and not collect_only:
        config.pluginmanager.register(RunTrustPlugin.from_env(run_id), INTEGRITY_PLUGIN_NAME)


def pytest_configure(config: pytest.Config) -> None:
    register(config)


__all__ = ["EXECUTED", "RunGatePlugin", "RunTrustPlugin", "register"]
