"""The contract and its documentation cannot drift: every example in the integration guide
validates against the schema it claims, and every schema is shown there."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from qaplatform import schemas

DOC = Path(__file__).resolve().parents[2] / "docs" / "integration_api.md"
_EXAMPLE = re.compile(r"<!-- validates: ([a-z0-9-]+) -->\s*```json\n(.*?)```", re.DOTALL)


def _examples() -> list[tuple[str, str]]:
    return _EXAMPLE.findall(DOC.read_text(encoding="utf-8"))


def test_the_guide_has_examples():
    assert len(_examples()) >= 8


@pytest.mark.parametrize("name, text", _examples(), ids=[n for n, _ in _examples()])
def test_guide_example_validates(name, text):
    assert schemas.errors_for(name, json.loads(text)) == []


def test_guide_mentions_every_request_schema():
    text = DOC.read_text(encoding="utf-8")
    for name in (
        "run-create",
        "results-batch",
        "test-result",
        "verdict-row",
        "evidence-batch",
        "run-finish",
        "run-summary",
        "traceability",
        "release-verdict",
        "run-request",
    ):
        assert f"`{name}`" in text, name


def test_request_schemas_reject_unknown_fields():
    for name in ("run-create", "test-result", "run-finish", "run-request", "evidence-link"):
        assert schemas.load(name)["additionalProperties"] is False, name


def test_validate_raises_with_pointers():
    with pytest.raises(schemas.SchemaError) as info:
        schemas.validate("test-result", {"test_id": "", "outcome": "passed"})
    assert info.value.errors[0]["pointer"] == "/test_id"


def test_date_time_format_is_enforced():
    assert schemas.is_datetime("2026-01-05T10:12:00Z")
    assert schemas.is_datetime("2026-01-05T10:12:00.5+01:00")
    assert not schemas.is_datetime("2026-01-05")
    assert not schemas.is_datetime("2026-01-05T10:12:00")
    assert not schemas.is_datetime("yesterday")
