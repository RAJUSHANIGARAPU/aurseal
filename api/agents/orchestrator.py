"""Orchestrator: coordinate generate → execute → analyze → heal → rerun loop.

Three of the loop's verdicts were wrong, and two of them in opposite
directions — which is why the fixes below are deliberately separate.

* **A run that never happened was a success.** The success gate was
  ``result.failed == 0 and result.errors == 0``, and an ExecutionResult that
  could not produce a verdict satisfies it with zeros. A pytest crash that left
  the previous run's report on disk reported ``success=True,
  final_pass_count=7`` off numbers no test in this run produced. The gate now
  asks ``result.ran`` first, and a run with no verdict stops the loop rather
  than being healed — healing cannot fix a usage error, and retrying it four
  times just spends four timeouts.
* **An all-green run was a failure.** With ``stop_on_success=False`` — a
  documented option — a suite where everything passed fell out of the loop into
  the exhausted branch, which hardcoded ``success=False``. Measured: 5 passed,
  0 failed, ``OrchestrationResult(success=False)``. The final verdict is now
  computed from the last run on every path.
* **A passing file was healed with another file's failures.** ``[a for a in
  analyses if gen_file.stem in a.test_name] or analyses`` — when a file had no
  failures the comprehension was empty and the ``or`` handed it *all* of them,
  so a green file was rewritten from unrelated diagnoses. The substring also
  cross-fired (``test_users`` matches ``test_users_admin.py``), and
  AnalysisAgent's fabricated ``unknown_test_N`` names match nothing at all, so
  they always took the fallback.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from api.agents.analysis import AnalysisAgent
from api.agents.execution import ExecutionAgent
from api.agents.generation import GenerationAgent
from api.agents.healing import SelfHealingAgent
from api.agents.ingestion import IngestionAgent
from api.engine.context_memory import ContextMemory
from api.engine.observability import AgentLogger

if TYPE_CHECKING:
    from api.llm.base import BaseLLMClient
    from memory.config import MemoryConfig

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Final result of an orchestration run.

    Args:
        error: why the run produced no verdict at all, as distinct from tests
            having failed. Empty when the suite actually ran; ``final_pass_count``
            and ``final_fail_count`` mean nothing when it is set.
    """

    success: bool
    total_runs: int
    final_pass_count: int
    final_fail_count: int
    healing_attempts: int
    report_path: Path | None
    session_id: str = ""
    failure_analyses: list = field(default_factory=list)
    error: str = ""


class Orchestrator:
    """Drives the full generate → execute → analyze → heal → rerun cycle.

    Args:
        collection_path: Path to a Postman Collection v2.1 JSON file.
        output_dir: Directory for generated test files.
        max_retries: Maximum heal-and-rerun cycles (default 3).
        stop_on_success: Exit immediately when all tests pass (default True).
        llm: Optional LLM client for enhanced generation, analysis, and healing.
        relax_status: Pass True to allow SelfHealingAgent to relax status assertions.
    """

    def __init__(
        self,
        collection_path: str | Path,
        output_dir: str | Path = "generated_tests",
        max_retries: int = 3,
        stop_on_success: bool = True,
        llm: BaseLLMClient | None = None,
        relax_status: bool = False,
        memory_config: MemoryConfig | None = None,
    ) -> None:
        self._collection_path = Path(collection_path)
        self._output_dir = Path(output_dir)
        self._max_retries = max_retries
        self._stop_on_success = stop_on_success
        self._llm = llm
        self._relax_status = relax_status

        self._session_id = uuid.uuid4().hex[:12]
        self._agent_logger = AgentLogger(self._session_id)
        self._memory = ContextMemory()
        self._mem_layer = self._init_memory_layer(memory_config)

    def run(self) -> OrchestrationResult:
        """Execute the full orchestration loop.

        Returns:
            OrchestrationResult describing the final state.
        """
        self._agent_logger.log("orchestrator", "start", {"collection": str(self._collection_path)})

        # Step 1: Ingest collection
        ingestion_agent = IngestionAgent()
        self._agent_logger.log("ingestion", "start")
        try:
            requests_list = ingestion_agent.parse_file(self._collection_path)
        except Exception as exc:  # noqa: BLE001
            self._agent_logger.log("ingestion", "error", {"error": str(exc)})
            return self._failure_result("Ingestion failed")

        self._agent_logger.log("ingestion", "complete", {"request_count": len(requests_list)})

        # Memory: retrieve relevant past failures and inject into context (active mode only)
        if self._mem_layer:
            self._mem_layer.before_execution(requests_list, self._memory)

        # Step 2: Generate tests
        gen_agent = GenerationAgent(
            output_dir=self._output_dir,
            llm=self._llm,
            memory=self._memory,
        )
        self._agent_logger.log("generation", "start", {"request_count": len(requests_list)})
        try:
            generated_files = gen_agent.generate(requests_list)
        except Exception as exc:  # noqa: BLE001
            self._agent_logger.log("generation", "error", {"error": str(exc)})
            return self._failure_result("Generation failed")

        self._agent_logger.log("generation", "complete", {"file_count": len(generated_files)})

        exec_agent = ExecutionAgent()
        analysis_agent = AnalysisAgent(llm=self._llm)
        healing_agent = SelfHealingAgent(llm=self._llm, relax_status_assertion=self._relax_status)

        total_runs = 0
        healing_attempts = 0
        last_analyses: list = []
        last_result = None

        for attempt in range(self._max_retries + 1):
            total_runs += 1
            label = "initial_run" if attempt == 0 else f"retry_{attempt}"

            self._agent_logger.log("execution", "start", {"attempt": label})
            result = exec_agent.run(generated_files)
            last_result = result

            if not result.ran:
                # No verdict: a crash, a usage error, nothing collected, a
                # timeout. Healing has nothing to work from and a rerun would
                # break the same way, so stop here rather than spending the
                # retry budget and then reporting on numbers nobody produced.
                self._agent_logger.log(
                    "execution",
                    "no_verdict",
                    {"attempt": label, "exit_code": result.exit_code, "reason": result.error},
                )
                return self._failure_result(result.error, total_runs=total_runs)

            self._agent_logger.log(
                "execution",
                "complete",
                {
                    "passed": result.passed,
                    "failed": result.failed,
                    "errors": result.errors,
                    "duration_s": round(result.duration, 2),
                },
            )

            if result.failed == 0 and result.errors == 0:
                if self._stop_on_success:
                    self._agent_logger.log("orchestrator", "success", {"attempt": label})
                    return OrchestrationResult(
                        success=True,
                        total_runs=total_runs,
                        final_pass_count=result.passed,
                        final_fail_count=0,
                        healing_attempts=healing_attempts,
                        report_path=self._report_path(exec_agent),
                        session_id=self._session_id,
                        failure_analyses=[],
                    )

            if attempt >= self._max_retries:
                break

            # Analyze failures
            self._agent_logger.log(
                "analysis", "start", {"failure_count": result.failed + result.errors}
            )
            analyses = analysis_agent.analyze(result)
            last_analyses = analyses
            self._agent_logger.log(
                "analysis", "complete", {"categories": self._summarize(analyses)}
            )

            # Memory: store failures and enrich with historical context
            if self._mem_layer and analyses:
                insights = self._mem_layer.after_execution(
                    analyses, requests_list, self._session_id
                )
                self._agent_logger.log("memory", "enriched", {"insights_count": len(insights)})

            # Heal — one healing pass per generated file, using only that
            # file's own failures. A file nothing failed in is left alone.
            self._agent_logger.log("healing", "start", {"file_count": len(generated_files)})
            attributed = 0
            for gen_file in generated_files:
                file_analyses = self._analyses_for(analyses, gen_file)
                if not file_analyses:
                    continue
                attributed += len(file_analyses)
                heal_result = healing_agent.heal(file_analyses, gen_file)
                if heal_result.fixed:
                    healing_attempts += 1
                    self._agent_logger.log(
                        "healing",
                        "fix_applied",
                        {"file": gen_file.name, "changes": heal_result.changes_made},
                    )
            if analyses and not attributed:
                # Every diagnosis named a file that is not in this run — the
                # fabricated unknown_test_N names do exactly this. Nothing was
                # healed, so the next run will be identical; say so out loud
                # rather than letting it look like a healing pass happened.
                self._agent_logger.log(
                    "healing",
                    "unattributed",
                    {"analysis_count": len(analyses)},
                )
            self._agent_logger.log("healing", "complete", {"healing_attempts": healing_attempts})

        # Loop finished without an early return: either the retries are spent,
        # or the last run was green and stop_on_success is off. The verdict
        # comes from the last run either way — hardcoding False here is what
        # reported an all-green suite as a failure.
        fail_count = last_result.failed + last_result.errors if last_result else 0
        pass_count = last_result.passed if last_result else 0
        success = last_result is not None and last_result.ran and fail_count == 0
        self._agent_logger.log(
            "orchestrator",
            "success" if success else "exhausted",
            {"total_runs": total_runs, "final_failures": fail_count},
        )
        return OrchestrationResult(
            success=success,
            total_runs=total_runs,
            final_pass_count=pass_count,
            final_fail_count=fail_count,
            healing_attempts=healing_attempts,
            report_path=self._report_path(exec_agent),
            session_id=self._session_id,
            failure_analyses=[] if success else last_analyses,
        )

    @staticmethod
    def _analyses_for(analyses: list, gen_file: Path) -> list:
        """The analyses whose pytest nodeid names this exact file.

        A nodeid is ``path/to/test_users.py::test_get``, so the file is the
        segment before the first ``::`` and comparing its name is a real path
        match. Anything without a ``::`` — AnalysisAgent's ``unknown_test_N``
        placeholders, or a bare function name — belongs to no file and is
        deliberately handed to none, rather than to all of them.
        """
        matches = []
        for analysis in analyses:
            nodeid = str(getattr(analysis, "test_name", ""))
            if "::" not in nodeid:
                continue
            if Path(nodeid.split("::", 1)[0]).name == gen_file.name:
                matches.append(analysis)
        return matches

    @staticmethod
    def _report_path(exec_agent: ExecutionAgent) -> Path | None:
        # The agent's own report file, not a hardcoded path — and only when
        # this run wrote it, since the agent clears it before every run.
        path = exec_agent.report_file
        return path if path.exists() else None

    @staticmethod
    def _summarize(analyses: list) -> dict:
        summary: dict[str, int] = {}
        for a in analyses:
            key = a.category.value
            summary[key] = summary.get(key, 0) + 1
        return summary

    @staticmethod
    def _init_memory_layer(config: MemoryConfig | None):  # noqa: ANN205
        """Lazily initialise the memory layer — only imported when enabled."""
        if config is None or not config.enabled:
            return None
        try:
            from memory.middleware import MemoryMiddleware
            from memory.retriever import MemoryRetriever
            from memory.store import MemoryStore

            store = MemoryStore(config)
            retriever = MemoryRetriever(top_k=config.similarity_top_k)
            return MemoryMiddleware(store, retriever, config)
        except Exception:  # noqa: BLE001
            logger.warning("Memory layer failed to initialise — running without memory")
            return None

    def _failure_result(self, reason: str, total_runs: int = 0) -> OrchestrationResult:
        # Counts stay at zero on purpose: this run measured nothing, and any
        # number here would be read as a result it did not produce.
        logger.error("Orchestration failed: %s", reason)
        return OrchestrationResult(
            success=False,
            total_runs=total_runs,
            final_pass_count=0,
            final_fail_count=0,
            healing_attempts=0,
            report_path=None,
            session_id=self._session_id,
            error=reason,
        )
