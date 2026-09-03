"""What SelfHealingAgent is allowed to write, and what counts as a fix.

Healing overwrites a file that currently works. Two things have to hold: the
replacement has to be Python, and "I changed the file" has to mean "I changed
something that runs" — otherwise a retry budget gets spent on comments.
"""

from __future__ import annotations

from pathlib import Path

from api.agents.analysis import FailureAnalysis, FailureCategory
from api.agents.healing import SelfHealingAgent
from tests.api_agent._fake_llm import FakeLLM

WORKING_CODE = (
    "def test_x():\n"
    '    response, _ = measure_request("GET", "http://example.com", timeout=30)\n'
    "    assert response.status_code == 200\n"
)


def _analysis(category: FailureCategory) -> FailureAnalysis:
    return FailureAnalysis(
        test_name="test_x",
        category=category,
        root_cause="something went wrong",
        suggested_fix="fix it",
    )


# --- Defect 4: unvalidated model output overwrote the working file ---


def test_truncated_llm_output_is_not_written(tmp_path: Path) -> None:
    """A completion cut off at the token ceiling is not a fixed file."""
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(WORKING_CODE, encoding="utf-8")

    # What a max_tokens cut-off looks like: valid right up to where it stops.
    llm = FakeLLM(text='def test_x():\n    response, _ = measure_request("GET", "http://exa')
    result = SelfHealingAgent(llm=llm).heal([_analysis(FailureCategory.UNKNOWN)], test_file)

    assert not result.fixed
    assert test_file.read_text(encoding="utf-8") == WORKING_CODE


def test_unparseable_llm_output_leaves_the_original_intact(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(WORKING_CODE, encoding="utf-8")

    llm = FakeLLM(text="def test_x(:\n    this is not python at all\n")
    result = SelfHealingAgent(llm=llm).heal([_analysis(FailureCategory.UNKNOWN)], test_file)

    assert not result.fixed
    assert test_file.read_text(encoding="utf-8") == WORKING_CODE


def test_llm_failure_is_not_detected_by_string_sniffing(tmp_path: Path) -> None:
    """An empty completion means the call did not happen — not an empty file."""
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(WORKING_CODE, encoding="utf-8")

    result = SelfHealingAgent(llm=FakeLLM(text="")).heal(
        [_analysis(FailureCategory.UNKNOWN)], test_file
    )

    assert not result.fixed
    assert test_file.read_text(encoding="utf-8") == WORKING_CODE


# --- Defect 5: a comment counted as a fix ---


def test_data_error_alone_is_not_a_fix(tmp_path: Path) -> None:
    """Prepending a comment changes nothing executable, so it is not healing."""
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(WORKING_CODE, encoding="utf-8")

    result = SelfHealingAgent().heal([_analysis(FailureCategory.DATA_ERROR)], test_file)

    assert not result.fixed
    assert result.changes_made == []
    assert test_file.read_text(encoding="utf-8") == WORKING_CODE


# --- Positive controls ---


def test_a_real_llm_fix_is_applied(tmp_path: Path) -> None:
    """A file that would refuse every fix satisfies every test above."""
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(WORKING_CODE, encoding="utf-8")

    fixed_code = (
        "def test_x():\n"
        '    response, _ = measure_request("GET", "http://example.com", timeout=90)\n'
        "    assert response.status_code == 201\n"
    )
    llm = FakeLLM(text=f"```python\n{fixed_code}```")
    result = SelfHealingAgent(llm=llm).heal([_analysis(FailureCategory.UNKNOWN)], test_file)

    assert result.fixed
    assert result.changes_made == ["Applied LLM-based fix"]
    assert test_file.read_text(encoding="utf-8").strip() == fixed_code.strip()
    assert llm.prompts, "the model was never asked"


def test_a_real_rule_fix_is_applied(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(WORKING_CODE, encoding="utf-8")

    result = SelfHealingAgent().heal([_analysis(FailureCategory.TIMEOUT_ERROR)], test_file)

    assert result.fixed
    assert "timeout=60" in test_file.read_text(encoding="utf-8")
