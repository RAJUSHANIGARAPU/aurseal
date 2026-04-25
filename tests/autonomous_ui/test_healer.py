"""Tests for autonomous_ui.healer — healing strategy dispatch and application."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autonomous_ui.healer import UIHealer
from autonomous_ui.models import FailureAnalysis, FailureType


def _analysis(
    failure_type: FailureType = FailureType.LOCATOR,
    confidence: str = "high",
    selectors: list[str] | None = None,
    llm_suggestion: str = "",
) -> FailureAnalysis:
    return FailureAnalysis(
        test_name="test_example",
        failure_type=failure_type,
        root_cause="element not found",
        confidence=confidence,
        selectors_mentioned=selectors or ['[data-testid="search-field"]'],
        llm_suggestion=llm_suggestion,
    )


@pytest.fixture()
def healer() -> UIHealer:
    llm = MagicMock()
    llm.complete.return_value = ""
    return UIHealer(llm=llm)


# ------------------------------------------------------------------
# Guard: low confidence → no healing
# ------------------------------------------------------------------


def test_low_confidence_skips_healing(healer: UIHealer) -> None:
    analysis = _analysis(failure_type=FailureType.LOCATOR, confidence="low")
    result = healer.heal(analysis)
    assert not result.applied
    assert result.strategy == "none"


def test_unknown_failure_type_skips_healing(healer: UIHealer) -> None:
    analysis = _analysis(failure_type=FailureType.UNKNOWN, confidence="high")
    result = healer.heal(analysis)
    assert not result.applied
    assert result.strategy == "none"


def test_navigation_failure_skips_healing(healer: UIHealer) -> None:
    analysis = _analysis(failure_type=FailureType.NAVIGATION, confidence="high")
    result = healer.heal(analysis)
    assert not result.applied


# ------------------------------------------------------------------
# Locator healing — locator registry patching
# ------------------------------------------------------------------


def test_locator_heal_patches_registry(tmp_path: Path, healer: UIHealer) -> None:
    registry = {
        "search_input": {"type": "testid", "value": "search-field"},
    }
    registry_file = tmp_path / "wiki_locators.json"
    registry_file.write_text(json.dumps(registry))

    llm_response = json.dumps(
        {
            "locator_key": "search_input",
            "new_locator": {"type": "css", "value": "input[name='q']"},
            "reasoning": "testid not found, using name attribute instead",
        }
    )
    healer._llm.complete.return_value = llm_response

    with patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert result.applied
    assert result.strategy == "locator_patch"
    patched = json.loads(registry_file.read_text())
    assert patched["search_input"] == {"type": "css", "value": "input[name='q']"}


def test_locator_heal_refuses_repeated_patch_for_same_key(tmp_path: Path) -> None:
    registry = {"search_input": {"type": "testid", "value": "search-field"}}
    registry_file = tmp_path / "wiki_locators.json"
    registry_file.write_text(json.dumps(registry))

    llm_response = json.dumps(
        {
            "locator_key": "search_input",
            "new_locator": {"type": "css", "value": "input"},
            "reasoning": "alternative",
        }
    )
    llm = MagicMock()
    llm.complete.return_value = llm_response
    healer = UIHealer(llm=llm)

    with patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file):
        first = healer.heal(_analysis(failure_type=FailureType.LOCATOR))
        second = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert first.applied
    assert not second.applied  # same key — loop guard triggered


def test_locator_heal_falls_back_to_retry_when_registry_missing(
    healer: UIHealer, tmp_path: Path
) -> None:
    missing = tmp_path / "no_such_file.json"
    with (
        patch("autonomous_ui.healer._LOCATOR_REGISTRY", missing),
        patch("autonomous_ui.healer._HEALING_OVERRIDES", tmp_path / "overrides.json"),
    ):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))
    assert result.strategy == "wait_retry"
    assert result.applied


def test_locator_heal_falls_back_to_retry_when_llm_returns_nothing(tmp_path: Path) -> None:
    registry = {"search_input": {"type": "testid", "value": "search-field"}}
    registry_file = tmp_path / "wiki_locators.json"
    registry_file.write_text(json.dumps(registry))

    llm = MagicMock()
    llm.complete.return_value = ""  # LLM returns nothing
    healer = UIHealer(llm=llm)

    with (
        patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file),
        patch("autonomous_ui.healer._HEALING_OVERRIDES", tmp_path / "overrides.json"),
    ):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))
    assert result.strategy == "wait_retry"
    assert result.applied


def test_locator_heal_handles_llm_json_with_markdown_fences(tmp_path: Path) -> None:
    registry = {"search_input": {"type": "testid", "value": "search-field"}}
    registry_file = tmp_path / "wiki_locators.json"
    registry_file.write_text(json.dumps(registry))

    llm_response = (
        '```json\n{"locator_key": "search_input", "new_locator": '
        '{"type": "css", "value": "input"}, "reasoning": "test"}\n```'
    )
    llm = MagicMock()
    llm.complete.return_value = llm_response
    healer = UIHealer(llm=llm)

    with patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert result.applied


# ------------------------------------------------------------------
# Timeout healing → retry recording
# ------------------------------------------------------------------


def test_timeout_without_selectors_records_retry(healer: UIHealer, tmp_path: Path) -> None:
    analysis = _analysis(failure_type=FailureType.TIMEOUT, selectors=[])
    overrides_file = tmp_path / "overrides.json"
    with patch("autonomous_ui.healer._HEALING_OVERRIDES", overrides_file):
        result = healer.heal(analysis)
    assert result.strategy == "wait_retry"
    assert result.applied
    saved = json.loads(overrides_file.read_text())
    assert "test_example" in saved["retry_tests"]


def test_retry_overrides_appends_without_duplicates(healer: UIHealer, tmp_path: Path) -> None:
    analysis = _analysis(failure_type=FailureType.TIMEOUT, selectors=[])
    overrides_file = tmp_path / "overrides.json"
    with patch("autonomous_ui.healer._HEALING_OVERRIDES", overrides_file):
        healer.heal(analysis)
        healer.heal(analysis)
    saved = json.loads(overrides_file.read_text())
    assert saved["retry_tests"].count("test_example") == 1


# ------------------------------------------------------------------
# Assertion healing
# ------------------------------------------------------------------


def test_assertion_heal_skips_when_confidence_medium(healer: UIHealer) -> None:
    analysis = _analysis(failure_type=FailureType.ASSERTION, confidence="medium")
    result = healer.heal(analysis)
    assert not result.applied
    assert result.strategy == "assertion_patch"


def test_assertion_heal_skips_when_test_file_not_found(healer: UIHealer) -> None:
    analysis = _analysis(
        failure_type=FailureType.ASSERTION,
        confidence="high",
        llm_suggestion="fix expected value",
    )
    with patch.object(healer, "_locate_test_file", return_value=None):
        result = healer.heal(analysis)
    assert not result.applied


def test_assertion_heal_calls_autofixer_when_file_found(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text("assert title == 'Dashboard'")

    llm = MagicMock()
    llm.complete.return_value = ""
    healer = UIHealer(llm=llm)

    analysis = _analysis(failure_type=FailureType.ASSERTION, confidence="high")

    with (
        patch.object(healer, "_locate_test_file", return_value=test_file),
        patch("autonomous_ui.healer.AutoFixer") as MockFixer,
    ):
        mock_instance = MockFixer.return_value
        mock_instance.fix_file.return_value = True
        result = healer.heal(analysis)

    assert result.strategy == "assertion_patch"
    mock_instance.fix_file.assert_called_once()


# ------------------------------------------------------------------
# JSON parsing helper
# ------------------------------------------------------------------


def test_parse_json_valid_dict(healer: UIHealer) -> None:
    raw = '{"key": "value"}'
    assert healer._parse_json(raw) == {"key": "value"}


def test_parse_json_with_fences(healer: UIHealer) -> None:
    raw = '```json\n{"key": "value"}\n```'
    assert healer._parse_json(raw) == {"key": "value"}


def test_parse_json_invalid_returns_none(healer: UIHealer) -> None:
    assert healer._parse_json("not json at all") is None


def test_parse_json_list_returns_none(healer: UIHealer) -> None:
    assert healer._parse_json("[1, 2, 3]") is None
