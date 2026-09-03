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


# ------------------------------------------------------------------
# A locator write must be validated in shape before it lands
# ------------------------------------------------------------------


def _registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "wiki_locators.json"
    path.write_text(json.dumps({"search_input": {"type": "testid", "value": "search-field"}}))
    return path


def _healer_returning(payload) -> UIHealer:
    llm = MagicMock()
    llm.complete.return_value = payload if isinstance(payload, str) else json.dumps(payload)
    return UIHealer(llm=llm)


def test_string_new_locator_is_refused(tmp_path: Path) -> None:
    # Probed against the unfixed healer: the model returned
    # "new_locator": "[data-testid=search-field]" as a STRING where a mapping
    # is expected, it was written straight to wiki_locators.json, and
    # core/base_page.py:16 then raised "string indices must be integers" for
    # every test using that key. One heal, whole page object down.
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning(
        {
            "locator_key": "search_input",
            "new_locator": "[data-testid=search-field]",
            "reasoning": "flat selector",
        }
    )
    with patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert not result.applied
    assert json.loads(registry_file.read_text())["search_input"]["type"] == "testid"


@pytest.mark.parametrize(
    "new_locator",
    [
        {"value": "search-field"},  # no type
        {"type": "quantum", "value": "x"},  # type base_page cannot resolve
        {"type": "css"},  # css with no value
        {"type": "css", "value": ""},  # css with an empty value
        {"type": "role"},  # role with no role name
        ["testid", "search-field"],  # a list, not a mapping
    ],
)
def test_malformed_new_locator_is_refused(tmp_path: Path, new_locator) -> None:
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning(
        {"locator_key": "search_input", "new_locator": new_locator, "reasoning": "x"}
    )
    with patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert not result.applied
    assert json.loads(registry_file.read_text())["search_input"] == {
        "type": "testid",
        "value": "search-field",
    }


@pytest.mark.parametrize(
    "new_locator",
    [
        {"type": "css", "value": "input[name='q']"},
        {"type": "role", "role": "button", "name": "Search"},
        {"type": "testid", "value": "search-box"},
        {"type": "text", "value": "Search", "exact": False},
        {"type": "placeholder", "value": "Search here"},
    ],
)
def test_well_shaped_new_locator_is_applied(tmp_path: Path, new_locator) -> None:
    # Positive control: validation that refuses everything is a broken healer,
    # not a safe one. Every shape core/base_page.resolve() understands must land.
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning(
        {"locator_key": "search_input", "new_locator": new_locator, "reasoning": "x"}
    )
    with patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert result.applied
    assert json.loads(registry_file.read_text())["search_input"] == new_locator


def test_unchanged_locator_is_not_reported_as_a_fix(tmp_path: Path) -> None:
    # The unfixed healer reported applied=True and "patched 'k': X → X" when the
    # model echoed the value already in the registry — a no-op counted as a fix.
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning(
        {
            "locator_key": "search_input",
            "new_locator": {"type": "testid", "value": "search-field"},
            "reasoning": "looks fine to me",
        }
    )
    with patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert not result.applied
    assert "→" not in result.details


def test_unknown_locator_key_is_not_reported_as_a_retry(tmp_path: Path) -> None:
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning(
        {
            "locator_key": "not_in_registry",
            "new_locator": {"type": "css", "value": "input"},
            "reasoning": "x",
        }
    )
    with (
        patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file),
        patch("autonomous_ui.healer._HEALING_OVERRIDES", tmp_path / "overrides.json"),
    ):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert not result.applied
    assert result.strategy != "wait_retry"


# ------------------------------------------------------------------
# A parse failure is not "no heal needed"
# ------------------------------------------------------------------


def test_truncated_json_is_not_reported_as_an_applied_heal(tmp_path: Path) -> None:
    # max_tokens cut the response mid-object. The unfixed healer turned that
    # into strategy="wait_retry", applied=True, "recorded for retry with
    # --reruns 2" — which then poisons the flakiness history via the reruns.
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning('{"locator_key": "search_input", "new_locator": {"type": "cs')

    with (
        patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file),
        patch("autonomous_ui.healer._HEALING_OVERRIDES", tmp_path / "overrides.json"),
    ):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert not result.applied
    assert result.strategy != "wait_retry"


def test_truncated_json_says_it_was_truncated(tmp_path: Path) -> None:
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning('{"locator_key": "search_input", "new_locator": {"type": "cs')

    with (
        patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file),
        patch("autonomous_ui.healer._HEALING_OVERRIDES", tmp_path / "overrides.json"),
    ):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert "truncat" in result.details.lower()


def test_prose_instead_of_json_is_not_reported_as_an_applied_heal(tmp_path: Path) -> None:
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning("I could not find that element in the DOM you gave me.")

    with (
        patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file),
        patch("autonomous_ui.healer._HEALING_OVERRIDES", tmp_path / "overrides.json"),
    ):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert not result.applied
    assert not (tmp_path / "overrides.json").exists()


def test_empty_response_still_records_a_retry(tmp_path: Path) -> None:
    # Positive control, and the line between the two cases: "the model was never
    # reached" is genuinely a no-answer and retry is the right fallback. Only a
    # response that arrived and could not be used is the new failure.
    registry_file = _registry_file(tmp_path)
    healer = _healer_returning("")

    with (
        patch("autonomous_ui.healer._LOCATOR_REGISTRY", registry_file),
        patch("autonomous_ui.healer._HEALING_OVERRIDES", tmp_path / "overrides.json"),
    ):
        result = healer.heal(_analysis(failure_type=FailureType.LOCATOR))

    assert result.applied
    assert result.strategy == "wait_retry"
