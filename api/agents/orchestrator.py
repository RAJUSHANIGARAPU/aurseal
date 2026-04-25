"""Orchestrator: coordinate generate → execute → analyze → heal → rerun loop."""

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

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Final result of an orchestration run."""

    success: bool
    total_runs: int
    final_pass_count: int
    final_fail_count: int
    healing_attempts: int
    report_path: Path | None
    session_id: str = ""
    failure_analyses: list = field(default_factory=list)


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
                        report_path=self._report_path(),
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

            # Heal — one healing pass per generated file
            self._agent_logger.log("healing", "start", {"file_count": len(generated_files)})
            for gen_file in generated_files:
                file_analyses = [a for a in analyses if gen_file.stem in a.test_name] or analyses
                heal_result = healing_agent.heal(file_analyses, gen_file)
                if heal_result.fixed:
                    healing_attempts += 1
                    self._agent_logger.log(
                        "healing",
                        "fix_applied",
                        {"file": gen_file.name, "changes": heal_result.changes_made},
                    )
            self._agent_logger.log("healing", "complete", {"healing_attempts": healing_attempts})

        # Exhausted retries
        fail_count = last_result.failed + last_result.errors if last_result else 0
        pass_count = last_result.passed if last_result else 0
        self._agent_logger.log(
            "orchestrator",
            "exhausted",
            {"total_runs": total_runs, "final_failures": fail_count},
        )
        return OrchestrationResult(
            success=False,
            total_runs=total_runs,
            final_pass_count=pass_count,
            final_fail_count=fail_count,
            healing_attempts=healing_attempts,
            report_path=self._report_path(),
            session_id=self._session_id,
            failure_analyses=last_analyses,
        )

    def _report_path(self) -> Path | None:
        p = Path("reports/pytest_report.json")
        return p if p.exists() else None

    @staticmethod
    def _summarize(analyses: list) -> dict:
        summary: dict[str, int] = {}
        for a in analyses:
            key = a.category.value
            summary[key] = summary.get(key, 0) + 1
        return summary

    def _failure_result(self, reason: str) -> OrchestrationResult:
        logger.error("Orchestration failed: %s", reason)
        return OrchestrationResult(
            success=False,
            total_runs=0,
            final_pass_count=0,
            final_fail_count=0,
            healing_attempts=0,
            report_path=None,
            session_id=self._session_id,
        )
