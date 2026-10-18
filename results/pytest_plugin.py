"""The results recorder: controller-only, xdist-safe writes into the results store.

Registered from the root ``conftest.py`` through :func:`register` (or ``-p
results.pytest_plugin``). What it does:

* **Single writer.** Only the controller (or a plain, non-distributed session) opens the
  database. xdist already replays every worker's ``TestReport`` to the controller,
  including ``user_properties`` and durations, so workers never open a connection and
  there is nothing to lock across processes.
* **Every attempt is a row.** A pytest-rerunfailures retry ends the attempt (outcome
  ``rerun``); the next reports belong to attempt n+1. The ``final_results`` view picks the
  last attempt, the same rule the run-trust gate applies. A second invocation with
  the same ``TEST_RUN_ID`` (``pytest --lf``) is a new ``invocations`` row of the same run,
  and its attempts continue the numbering.
* **Selected but never reported** tests are stored as ``not_run``, so a crashed worker
  shows up as a gap instead of a smaller green run.
* **Side channels.** At session end the controller ingests this run's
  ``verdicts.jsonl`` and data-origin rows (idempotent) and the integrity verdict.
* **A store failure never changes a test outcome**, and is never silent: the terminal
  summary says ``results-store: DEGRADED (<reason>)`` and
  ``$RESULTS_DIR/store-status.json`` records it. ``RESULTS_STORE_REQUIRED=true`` turns
  the failure into a non-zero exit (``INTERNAL_ERROR``).

Off switch: ``RESULTS_STORE=off``. ``--collect-only`` records nothing.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from core.logger import logger
from gate._env import env_bool, env_str
from gate.integrity import Outcome
from results._util import default_db_path, from_epoch, results_dir, utc_now
from results.evidence import EVIDENCE_PROPERTY
from results.models import EvidenceRecord, ResultRecord, RunRecord

# The engine user property set by reporting.pytest_plugin.tag_browser.
BROWSER_PROPERTY = "browser"

PLUGIN_NAME = "results-recorder"
STORE_ENV = "RESULTS_STORE"
REQUIRED_ENV = "RESULTS_STORE_REQUIRED"
LANE_ENV = "RESULTS_LANE"
STATUS_FILE = "store-status.json"
DEFECT_PROPERTY = "known_defect"
FLUSH_EVERY = 200
FLUSH_SECONDS = 5.0
NOT_RUN_MESSAGE = "selected but never reported (interrupted or crashed?)"

_EXC_TYPE = re.compile(r"^([A-Za-z_][\w.]*)\s*:")
_BUILTIN_MARKERS = frozenset(
    {"skip", "skipif", "xfail", "parametrize", "usefixtures", "filterwarnings", "flaky"}
)


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else int(round(seconds * 1000))


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        text = str(longrepr[2])
        return text[len("Skipped: ") :] if text.startswith("Skipped: ") else text
    return str(longrepr or "")[:500]


def _worker(report: pytest.TestReport) -> str:
    node = getattr(report, "node", None)
    gateway = getattr(node, "gateway", None)
    if gateway is not None and getattr(gateway, "id", None):
        return str(gateway.id)
    worker_id = getattr(report, "worker_id", None)
    return str(worker_id) if worker_id else "main"


def param_id(nodeid: str) -> str | None:
    if nodeid.endswith("]") and "[" in nodeid:
        return nodeid[nodeid.index("[") + 1 : -1]
    return None


def failure_fingerprint(exc_type: str, path: str | None, lineno: int | None) -> str | None:
    """sha1 of the exception type plus the top (crash) frame; None when nothing failed."""
    if not exc_type and not path:
        return None
    import hashlib

    location = f"{Path(path).as_posix() if path else ''}:{lineno if lineno is not None else ''}"
    return hashlib.sha1(f"{exc_type}|{location}".encode()).hexdigest()[:16]


def _crash(report: pytest.TestReport) -> tuple[str | None, str | None]:
    """(message, fingerprint) of a failed report."""
    crash = getattr(report.longrepr, "reprcrash", None)
    if crash is None:
        text = str(report.longrepr or "").strip()
        first = text.splitlines()[-1] if text else ""
        return (first or None), failure_fingerprint(_exc_type(first), None, None)
    message = str(getattr(crash, "message", "") or "")
    path = getattr(crash, "path", None)
    rel = path
    try:
        rel = os.path.relpath(path) if path else path
    except ValueError:
        pass
    return message or None, failure_fingerprint(
        _exc_type(message), rel, getattr(crash, "lineno", None)
    )


def _exc_type(message: str) -> str:
    match = _EXC_TYPE.match(message.splitlines()[0] if message else "")
    return match.group(1) if match else ""


class RecorderExtension:
    """Hook points for features layered on the recorder (case catalogue, revisions).

    ``observe`` sees every report of an attempt and may stash data in ``extra``;
    ``enrich`` returns the attempt's result record, possibly changed; ``before_flush``
    runs before a batch is written (e.g. to upsert the cases it references);
    ``after_run`` runs once at session end, before the run is finished.
    """

    def observe(self, report: pytest.TestReport, extra: dict[str, Any]) -> None:
        return None

    def enrich(self, record: ResultRecord, extra: dict[str, Any]) -> ResultRecord:
        return record

    def before_flush(self, store: Any, rows: list[ResultRecord]) -> None:
        return None

    def after_run(self, store: Any, run_id: str) -> None:
        return None


class _Attempt:
    """One attempt's phases while its reports arrive."""

    __slots__ = (
        "outcome",
        "reason",
        "defect",
        "cleared",
        "phases",
        "start",
        "stop",
        "worker",
        "message",
        "longrepr",
        "fingerprint",
        "evidence",
        "markers",
        "extra",
        "browser",
    )

    def __init__(self) -> None:
        self.outcome: Outcome | None = None
        self.reason = ""
        self.defect: str | None = None
        self.cleared = False
        self.phases: dict[str, float] = {}
        self.start: float | None = None
        self.stop: float | None = None
        self.worker = "main"
        self.message: str | None = None
        self.longrepr: str | None = None
        self.fingerprint: str | None = None
        self.evidence: list[dict[str, Any]] = []
        self.markers: tuple[str, ...] = ()
        self.extra: dict[str, Any] = {}
        self.browser: str | None = None

    def failed_report(self, report: pytest.TestReport) -> None:
        if self.message is None:
            self.message, self.fingerprint = _crash(report)
            self.longrepr = report.longreprtext or None


class ResultsRecorder:
    """Buffers final attempts on the controller and writes them in batched transactions."""

    def __init__(
        self,
        config: pytest.Config,
        run_id: str,
        *,
        db_url: str | None = None,
        status_dir: str | Path | None = None,
        required: bool = False,
        flush_every: int = FLUSH_EVERY,
        flush_seconds: float = FLUSH_SECONDS,
        verdicts_file: str | Path | None = None,
        provenance_file: str | Path | None = None,
    ) -> None:
        self.config = config
        self.run_id = run_id
        self.db_url = db_url
        self.status_dir = Path(status_dir) if status_dir else results_dir()
        self.required = required
        self.flush_every = flush_every
        self.flush_seconds = flush_seconds
        self.verdicts_file = verdicts_file
        self.provenance_file = provenance_file
        self.store: Any = None
        self.degraded: str | None = None
        self.seq: int | None = None
        self.written = 0
        self.workers = 0
        self._offsets: dict[str, int] = {}
        self._local_attempt: dict[str, int] = {}
        self._open: dict[str, _Attempt] = {}
        self._finished: set[str] = set()
        self._selected: list[str] = []
        self._buffer: list[ResultRecord] = []
        self._evidence: list[EvidenceRecord] = []
        self._last_flush = time.monotonic()
        self._known_markers = self._marker_names(config)
        self.extensions: list[RecorderExtension] = []

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _marker_names(config: pytest.Config) -> frozenset[str]:
        names = set(_BUILTIN_MARKERS) | {DEFECT_PROPERTY}
        try:
            for line in config.getini("markers"):
                names.add(re.split(r"[(:\s]", str(line).strip(), maxsplit=1)[0])
        except (ValueError, KeyError):
            pass
        return frozenset(n for n in names if n)

    def _degrade(self, reason: str) -> None:
        if self.degraded is None:
            self.degraded = reason
            logger.error("[results-store] DEGRADED: %s", reason)
        self._buffer.clear()
        self._evidence.clear()
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass
        self.store = None

    def _run_record(self) -> RunRecord:
        env = os.environ
        ci_url = env.get("BUILD_URL")
        if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
            server = env.get("GITHUB_SERVER_URL", "https://github.com")
            ci_url = f"{server}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
        provider = (
            "github-actions"
            if env.get("GITHUB_ACTIONS")
            else ("jenkins" if env.get("JENKINS_URL") else None)
        )
        try:
            environment = self.config.getoption("--env", default=None)
        except ValueError:
            environment = None
        from gate.schedules import schedule_name_from_env, trigger_from_env

        try:
            trigger: str | None = trigger_from_env()
            schedule_name = schedule_name_from_env()
        except ValueError:
            # A bad RUN_TRIGGER must not stop the recorder; the run keeps no trigger.
            trigger, schedule_name = None, None
        return RunRecord(
            run_id=self.run_id,
            source="pytest",
            framework="pytest",
            lane=env_str(LANE_ENV, None) or env_str("RUN_TRUST_LANE", None),
            environment=environment or env_str("TEST_ENV", None),
            sut_version=env_str("SUT_VERSION", None),
            git_sha=self._git_sha(),
            git_branch=env_str("GITHUB_HEAD_REF", None) or env_str("GITHUB_REF_NAME", None),
            ci_provider=provider,
            ci_url=ci_url,
            trigger=trigger,
            schedule_name=schedule_name,
            release_label=env_str("RELEASE_LABEL", None),
            host=socket.gethostname(),
            started_at=utc_now(),
        )

    def _shard_index(self) -> int | None:
        """K of ``--shard=K/N`` when the shard plugin is loaded; None otherwise."""
        try:
            text = self.config.getoption("--shard", default=None)
        except ValueError:
            return None
        if not text:
            return None
        from gate.shard import parse_shard

        try:
            return parse_shard(str(text)).index
        except ValueError:
            return None

    def _git_sha(self) -> str | None:
        from results.revision import run_git_sha

        return run_git_sha(Path(str(self.config.rootpath)))

    # ------------------------------------------------------------- session

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        from results.store import open_store

        try:
            self.store = open_store(self.db_url)
            run = self._run_record()
            self.store.begin_run(run)
            self.seq = self.store.begin_invocation(
                self.run_id, list(self.config.invocation_params.args), None, lane=run.lane
            )
            self._offsets = self.store.max_attempts(self.run_id)
        except Exception as exc:
            self._degrade(f"cannot open the store: {exc}")

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self._selected.extend(item.nodeid for item in session.items)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids: list[str]) -> None:
        if not self._selected:
            self._selected.extend(ids)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodeready(self, node) -> None:
        self.workers += 1

    # ------------------------------------------------------------- reports

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        nodeid = report.nodeid
        attempt = self._open.get(nodeid)
        if attempt is None:
            if report.when == "teardown" and nodeid in self._finished:
                return  # teardown of an attempt already closed by an outcome without one
            attempt = self._open[nodeid] = _Attempt()
            attempt.worker = _worker(report)
            attempt.markers = tuple(sorted(k for k in report.keywords if k in self._known_markers))
        attempt.phases[report.when] = attempt.phases.get(report.when, 0.0) + (
            report.duration or 0.0
        )
        start, stop = getattr(report, "start", None), getattr(report, "stop", None)
        if start is not None and (attempt.start is None or start < attempt.start):
            attempt.start = start
        if stop is not None:
            attempt.stop = max(attempt.stop or stop, stop)
        for key, value in report.user_properties:
            if key == DEFECT_PROPERTY and isinstance(value, str):
                attempt.defect = value
            elif key == BROWSER_PROPERTY and isinstance(value, str):
                attempt.browser = value
            elif key == EVIDENCE_PROPERTY and isinstance(value, dict):
                if value not in attempt.evidence:
                    attempt.evidence.append(value)
        for ext in self.extensions:
            ext.observe(report, attempt.extra)

        outcome = report.outcome
        if outcome == "rerun":
            # This attempt failed and will be retried: close it as a failed attempt.
            attempt.outcome = Outcome.ERROR if report.when != "call" else Outcome.FAILED
            attempt.failed_report(report)
            self._close(nodeid)
            return
        if report.when == "setup":
            if outcome == "failed":
                attempt.outcome = Outcome.ERROR
                attempt.failed_report(report)
            elif outcome == "skipped":
                attempt.outcome, attempt.reason = Outcome.SKIPPED, _skip_reason(report)
        elif report.when == "call":
            if outcome == "passed":
                attempt.outcome = Outcome.PASSED
            elif outcome == "failed":
                attempt.outcome = Outcome.FAILED
                attempt.cleared = str(report.longrepr or "").startswith("[XPASS(strict)]")
                attempt.failed_report(report)
            elif hasattr(report, "wasxfail"):
                attempt.outcome = Outcome.XFAILED
                attempt.reason = str(getattr(report, "wasxfail", "") or "")
            else:
                attempt.outcome, attempt.reason = Outcome.SKIPPED, _skip_reason(report)
        elif report.when == "teardown":
            if outcome == "failed":
                if attempt.outcome in (None, Outcome.PASSED):
                    attempt.outcome = Outcome.ERROR
                attempt.failed_report(report)
            self._close(nodeid)

    def _close(self, nodeid: str) -> None:
        attempt = self._open.pop(nodeid, None)
        if attempt is None:
            return
        self._finished.add(nodeid)
        local = self._local_attempt.get(nodeid, 0) + 1
        self._local_attempt[nodeid] = local
        number = self._offsets.get(nodeid, 0) + local
        outcome = attempt.outcome or Outcome.NOT_RUN
        duration = sum(attempt.phases.values())
        record = ResultRecord(
            run_id=self.run_id,
            nodeid=nodeid,
            outcome=outcome.value,
            attempt=number,
            invocation=self.seq,
            param_id=param_id(nodeid),
            is_cleared=attempt.cleared,
            duration_ms=_ms(duration),
            setup_ms=_ms(attempt.phases.get("setup")),
            call_ms=_ms(attempt.phases.get("call")),
            teardown_ms=_ms(attempt.phases.get("teardown")),
            started_at=from_epoch(attempt.start),
            finished_at=from_epoch(attempt.stop),
            worker=attempt.worker,
            browser=attempt.browser,
            shard_index=self._shard_index(),
            message=attempt.message
            or (attempt.reason if outcome in (Outcome.SKIPPED, Outcome.XFAILED) else None),
            longrepr=attempt.longrepr,
            failure_fingerprint=attempt.fingerprint,
            skip_reason=attempt.reason if outcome is Outcome.SKIPPED else None,
            defect_ref=attempt.defect,
            markers=attempt.markers,
        )
        for ext in self.extensions:
            record = ext.enrich(record, attempt.extra)
        self._buffer.append(record)
        for ev in attempt.evidence:
            self._evidence.append(
                EvidenceRecord(
                    run_id=self.run_id,
                    nodeid=nodeid,
                    attempt=number,
                    kind=str(ev.get("kind", "other")),
                    uri=str(ev.get("uri", "")),
                    sha256=ev.get("sha256"),
                    bytes=ev.get("bytes"),
                    created_at=ev.get("created_at"),
                )
            )
        if (
            len(self._buffer) >= self.flush_every
            or time.monotonic() - self._last_flush >= self.flush_seconds
        ):
            self.flush()

    def flush(self) -> None:
        """Write buffered rows in one transaction; a failure degrades, never raises."""
        self._last_flush = time.monotonic()
        if self.store is None:
            self._buffer.clear()
            self._evidence.clear()
            return
        rows, evidence = list(self._buffer), list(self._evidence)
        self._buffer.clear()
        self._evidence.clear()
        try:
            for ext in self.extensions:
                ext.before_flush(self.store, rows)
            with self.store.transaction():
                self.written += self.store.add_results(rows)
                if evidence:
                    self.store.add_evidence(evidence)
        except Exception as exc:
            self._degrade(f"write failed: {exc}")

    # ------------------------------------------------------------- finish

    def _not_run(self) -> Iterable[ResultRecord]:
        for nodeid in dict.fromkeys(self._selected):
            if nodeid in self._finished or nodeid in self._open:
                continue
            yield ResultRecord(
                run_id=self.run_id,
                nodeid=nodeid,
                outcome=Outcome.NOT_RUN.value,
                attempt=self._offsets.get(nodeid, 0) + 1,
                invocation=self.seq,
                param_id=param_id(nodeid),
                message=NOT_RUN_MESSAGE,
            )

    def _integrity_verdict(self) -> str | None:
        plugin = self.config.pluginmanager.get_plugin("run-trust")
        result = getattr(plugin, "result", None)
        verdict = getattr(result, "verdict", None)
        return getattr(verdict, "value", None)

    @pytest.hookimpl(wrapper=True, trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int):
        # A wrapper with trylast is the innermost one: this runs after every plain
        # sessionfinish (the run-trust verdict included) and before the terminal
        # summary is printed.
        result = yield
        self.finish(session)
        return result

    def finish(self, session: pytest.Session) -> None:
        for nodeid in list(self._open):
            self._close(nodeid)
        self._buffer.extend(self._not_run())
        self.flush()
        status = int(session.exitstatus)
        if self.store is not None:
            try:
                for ext in self.extensions:
                    ext.after_run(self.store, self.run_id)
                self.store.ingest_verdicts(self.verdicts_file, self.run_id)
                self.store.ingest_provenance(self.provenance_file, self.run_id)
                self.store.finish_invocation(self.run_id, self.seq, status, self.workers or None)
                run_status = "interrupted" if status == pytest.ExitCode.INTERRUPTED else "finished"
                self.store.finish_run(self.run_id, run_status, self._integrity_verdict())
            except Exception as exc:
                self._degrade(f"finishing the run failed: {exc}")
        self._write_status()
        if self.store is not None:
            self.store.close()
            self.store = None
        if self.degraded and self.required and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.INTERNAL_ERROR

    def _write_status(self) -> None:
        body = {
            "run_id": self.run_id,
            "status": "degraded" if self.degraded else "ok",
            "reason": self.degraded,
            "db": str(self.db_url or default_db_path()),
            "invocation": self.seq,
            "results_written": self.written,
            "required": self.required,
            "timestamp": utc_now(),
        }
        try:
            self.status_dir.mkdir(parents=True, exist_ok=True)
            (self.status_dir / STATUS_FILE).write_text(json.dumps(body, indent=2), "utf-8")
        except OSError as exc:
            logger.error("[results-store] could not write %s: %s", STATUS_FILE, exc)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if self.degraded:
            terminalreporter.write_line(
                f"results-store: DEGRADED ({self.degraded})", red=True, bold=True
            )
        else:
            terminalreporter.write_line(
                f"results-store: {self.written} result row(s) recorded for run {self.run_id}"
            )


def _store_enabled() -> bool:
    return (env_str(STORE_ENV, "on") or "on").lower() not in ("off", "0", "false", "no")


CASE_ATTR = "results_case"
REVISION_ATTR = "results_revision"
STAMPER_NAME = "results-case-stamper"


class CaseStamper:
    """Runs where the tests run (each xdist worker, or the single process).

    Puts the test's catalogue case on its setup report as ``report.results_case``.
    pytest serialises a report's whole ``__dict__`` across xdist, so the controller
    receives the case with the report and never needs the item (it has none under xdist).
    """

    def __init__(self, rootdir: Path | None = None) -> None:
        self._cache: dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None]] = {}
        self._rootdir = rootdir
        self._git_sha: str | None = None
        self._git_resolved = False

    def _sha(self) -> str | None:
        if not self._git_resolved:
            from results.revision import run_git_sha

            self._git_sha = run_git_sha(self._rootdir or Path.cwd())
            self._git_resolved = True
        return self._git_sha

    def case_for(self, item: pytest.Item) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """(case, revision) as JSON dicts; (None, None) when the item cannot be described."""
        if item.nodeid not in self._cache:
            from dataclasses import replace

            from results.catalogue import case_from_item
            from results.revision import revision_from_item

            try:
                case = case_from_item(item)
                rev = revision_from_item(item, case, git_sha=self._sha())
                if rev is not None:
                    case = replace(case, current_revision_id=rev.revision_id)
                self._cache[item.nodeid] = (case.to_json(), rev.to_json() if rev else None)
            except Exception as exc:  # measurement must never fail a test
                logger.warning("[results-store] no catalogue case for %s: %s", item.nodeid, exc)
                self._cache[item.nodeid] = (None, None)
        return self._cache[item.nodeid]

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo):
        report = yield
        if call.when == "setup":
            case, rev = self.case_for(item)
            if case is not None:
                setattr(report, CASE_ATTR, case)
            if rev is not None:
                setattr(report, REVISION_ATTR, rev)
        return report


class CatalogueExtension(RecorderExtension):
    """Controller side: stamps result rows with their case and revision.

    The cases and revisions a batch references are written before the batch, so the
    rows' foreign keys always resolve. A revision seen for the first time records this
    run as its ``first_seen_run_id``.
    """

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._pending_revs: dict[tuple[str, str], Any] = {}
        self._stored: set[str] = set()
        self._stored_revs: set[tuple[str, str]] = set()

    def observe(self, report: pytest.TestReport, extra: dict[str, Any]) -> None:
        case = getattr(report, CASE_ATTR, None)
        if isinstance(case, dict) and case.get("case_id"):
            extra["case"] = case
        rev = getattr(report, REVISION_ATTR, None)
        if isinstance(rev, dict) and rev.get("revision_id"):
            extra["revision"] = rev

    def enrich(self, record: ResultRecord, extra: dict[str, Any]) -> ResultRecord:
        from dataclasses import replace

        from results.models import CaseRecord, RevisionRecord

        case = extra.get("case")
        if not case:
            return record
        if case["case_id"] not in self._stored:
            self._pending[case["case_id"]] = CaseRecord.from_json(case)
        rev = extra.get("revision")
        if not rev:
            return replace(record, case_id=case["case_id"])
        key = (case["case_id"], rev["revision_id"])
        if key not in self._stored_revs:
            self._pending_revs[key] = replace(
                RevisionRecord.from_json(rev), first_seen_run_id=record.run_id
            )
        return replace(record, case_id=case["case_id"], revision_id=rev["revision_id"])

    def before_flush(self, store: Any, rows: list[ResultRecord]) -> None:
        if self._pending:
            store.upsert_cases(list(self._pending.values()))
            self._stored.update(self._pending)
            self._pending.clear()
        if self._pending_revs:
            store.add_revisions(list(self._pending_revs.values()))
            self._stored_revs.update(self._pending_revs)
            self._pending_revs.clear()


class SearchExtension(RecorderExtension):
    """Brings the search index up to date at the end of the run.

    A stale index is not lost results, so a reindex failure is logged and leaves the
    store's status alone.
    """

    def after_run(self, store: Any, run_id: str) -> None:
        from results.search import reindex

        try:
            reindex(store)
        except Exception as exc:
            logger.warning("[results-store] search reindex failed: %s", exc)


def register(config: pytest.Config) -> ResultsRecorder | None:
    """Register the recorder on the controller; a no-op on workers, collect-only or when off.

    Workers (and a plain single-process session) also get the :class:`CaseStamper`, which
    only annotates reports and never touches the database.
    """
    from results.catalogue import register_markers

    register_markers(config)
    if config.pluginmanager.hasplugin(PLUGIN_NAME):
        return config.pluginmanager.get_plugin(PLUGIN_NAME)
    if not _store_enabled():
        return None
    if getattr(config.option, "collectonly", False):
        return None
    if not config.pluginmanager.hasplugin(STAMPER_NAME):
        config.pluginmanager.register(CaseStamper(Path(str(config.rootpath))), STAMPER_NAME)
    if hasattr(config, "workerinput"):
        return None
    from gate.run_id import ensure_run_id

    recorder = ResultsRecorder(
        config,
        ensure_run_id(config),
        db_url=env_str("RESULTS_DB", None),
        required=env_bool(REQUIRED_ENV, False),
    )
    recorder.extensions.append(CatalogueExtension())
    recorder.extensions.append(SearchExtension())
    config.pluginmanager.register(recorder, PLUGIN_NAME)
    return recorder


def pytest_configure(config: pytest.Config) -> None:
    register(config)


__all__ = [
    "PLUGIN_NAME",
    "CaseStamper",
    "CatalogueExtension",
    "RecorderExtension",
    "ResultsRecorder",
    "SearchExtension",
    "failure_fingerprint",
    "param_id",
    "register",
]
