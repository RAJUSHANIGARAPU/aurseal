"""End-to-end tests for the Orchestrator against httpbin.org.

These tests use the real httpbin.org endpoint and do NOT require an API key.
LLM calls are skipped automatically when ANTHROPIC_API_KEY is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.agents.analysis import AnalysisAgent, FailureAnalysis, FailureCategory
from api.agents.execution import ExecutionResult
from api.agents.generation import GenerationAgent
from api.agents.healing import SelfHealingAgent
from api.agents.ingestion import IngestionAgent, PostmanRequest
from api.agents.orchestrator import OrchestrationResult, Orchestrator
from api.engine.context_memory import ContextMemory

_SAMPLE_COLLECTION = (
    Path(__file__).parent.parent.parent / "api" / "postman" / "sample_collection.json"
)
_OUTPUT_DIR = Path("generated_tests") / "test_orchestrator_run"


@pytest.fixture(scope="module")
def sample_requests() -> list[PostmanRequest]:
    agent = IngestionAgent()
    return agent.parse_file(_SAMPLE_COLLECTION)


@pytest.fixture(scope="module")
def generated_files(sample_requests: list[PostmanRequest], tmp_path_factory) -> list[Path]:
    out = tmp_path_factory.mktemp("gen_tests")
    agent = GenerationAgent(output_dir=out)
    return agent.generate(sample_requests)


# --- Unit tests (no network) ---


def test_orchestrator_result_dataclass() -> None:
    result = OrchestrationResult(
        success=True,
        total_runs=1,
        final_pass_count=6,
        final_fail_count=0,
        healing_attempts=0,
        report_path=None,
        session_id="abc123",
    )
    assert result.success
    assert result.total_runs == 1


def test_context_memory_set_get() -> None:
    mem = ContextMemory()
    mem.set("key1", "value1")
    assert mem.get("key1") == "value1"
    assert mem.get("missing", "default") == "default"


def test_context_memory_extract_from_response() -> None:
    mem = ContextMemory()
    mem.extract_from_response(
        {"user": {"id": 42, "name": "Alice"}},
        {"uid": "user.id", "uname": "user.name"},
    )
    assert mem.get("uid") == 42
    assert mem.get("uname") == "Alice"


def test_context_memory_clear() -> None:
    mem = ContextMemory()
    mem.set("x", 1)
    mem.clear()
    assert mem.all() == {}


def test_context_memory_all() -> None:
    mem = ContextMemory()
    mem.set("a", 1)
    mem.set("b", 2)
    snapshot = mem.all()
    assert snapshot == {"a": 1, "b": 2}


def test_analysis_agent_categorises_timeout() -> None:
    agent = AnalysisAgent()
    mock_result = ExecutionResult(
        passed=0,
        failed=1,
        failure_details=[{"test_name": "test_x", "message": "ReadTimeout: timed out"}],
    )
    analyses = agent.analyze(mock_result)
    assert len(analyses) == 1
    assert analyses[0].category == FailureCategory.TIMEOUT_ERROR


def test_analysis_agent_categorises_assertion() -> None:
    agent = AnalysisAgent()
    mock_result = ExecutionResult(
        passed=0,
        failed=1,
        failure_details=[
            {"test_name": "test_y", "message": "AssertionError: expected 200 got 404"}
        ],
    )
    analyses = agent.analyze(mock_result)
    assert analyses[0].category == FailureCategory.ASSERTION_ERROR


def test_analysis_agent_categorises_connection_error() -> None:
    agent = AnalysisAgent()
    mock_result = ExecutionResult(
        passed=0,
        failed=1,
        failure_details=[
            {"test_name": "test_z", "message": "ConnectionError: Failed to establish connection"}
        ],
    )
    analyses = agent.analyze(mock_result)
    assert analyses[0].category == FailureCategory.ENV_ERROR


def test_analysis_agent_categorises_api_error_5xx() -> None:
    agent = AnalysisAgent()
    mock_result = ExecutionResult(
        passed=0,
        failed=1,
        failure_details=[{"test_name": "test_w", "message": "500 Internal Server Error"}],
    )
    analyses = agent.analyze(mock_result)
    assert analyses[0].category == FailureCategory.API_ERROR


def test_healing_agent_doubles_timeout(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sample.py"
    code = (
        "def test_x():\n"
        '    response, _ = measure_request("GET", "http://example.com", timeout=30)\n'
    )
    test_file.write_text(code)
    agent = SelfHealingAgent()
    analysis = FailureAnalysis(
        test_name="test_x",
        category=FailureCategory.TIMEOUT_ERROR,
        root_cause="timed out",
        suggested_fix="increase timeout",
    )
    result = agent.heal([analysis], test_file)
    assert result.fixed
    content = test_file.read_text()
    assert "timeout=60" in content


def test_healing_agent_no_change_when_category_not_handled(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sample2.py"
    code = "def test_y():\n    assert True\n"
    test_file.write_text(code)
    agent = SelfHealingAgent()
    analysis = FailureAnalysis(
        test_name="test_y",
        category=FailureCategory.UNKNOWN,
        root_cause="unknown",
        suggested_fix="inspect manually",
    )
    result = agent.heal([analysis], test_file)
    assert not result.fixed


def test_generation_creates_files(generated_files: list[Path]) -> None:
    assert len(generated_files) >= 1
    for path in generated_files:
        assert path.exists()
        assert path.suffix == ".py"


def test_generated_files_contain_test_functions(generated_files: list[Path]) -> None:
    for path in generated_files:
        content = path.read_text()
        assert "def test_" in content


def test_generated_files_import_validation(generated_files: list[Path]) -> None:
    for path in generated_files:
        content = path.read_text()
        assert "ValidationEngine" in content or "measure_request" in content


# --- End-to-end test against httpbin.org ---


@pytest.mark.e2e
def test_orchestrator_full_run_against_httpbin(tmp_path: Path) -> None:
    """Full orchestration run against httpbin.org must succeed.

    Uses template-based generation (no LLM required).
    httpbin.org is a stable public test API.
    """
    result = Orchestrator(
        collection_path=_SAMPLE_COLLECTION,
        output_dir=tmp_path / "gen",
        max_retries=2,
        llm=None,
    ).run()

    assert isinstance(result, OrchestrationResult)
    assert result.total_runs >= 1
    assert result.session_id != ""
    assert result.success, (
        f"Orchestration failed. "
        f"passed={result.final_pass_count}, failed={result.final_fail_count}, "
        f"runs={result.total_runs}"
    )
    assert result.final_pass_count >= 1
    assert result.final_fail_count == 0


def test_orchestrator_missing_collection_returns_failure(tmp_path: Path) -> None:
    """Orchestrator must return success=False when the collection file does not exist."""
    result = Orchestrator(
        collection_path=tmp_path / "nonexistent.json",
        output_dir=tmp_path / "gen",
        max_retries=1,
    ).run()
    assert not result.success


def test_orchestrator_no_retries_on_success(tmp_path: Path) -> None:
    """When all tests pass on the first run, no retries should occur."""
    result = Orchestrator(
        collection_path=_SAMPLE_COLLECTION,
        output_dir=tmp_path / "gen",
        max_retries=3,
        llm=None,
    ).run()
    assert result.success
    assert result.total_runs == 1  # stopped on success
