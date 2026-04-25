"""Tests for DynamicDataEngine — variable resolution via Faker, env vars, and context."""

from __future__ import annotations

import re

import pytest

from api.engine.context_memory import ContextMemory
from api.engine.dynamic_data import FAKER_MAP, DynamicDataEngine


@pytest.fixture
def engine() -> DynamicDataEngine:
    return DynamicDataEngine()


@pytest.fixture
def engine_with_memory() -> DynamicDataEngine:
    memory = ContextMemory()
    memory.set("session_token", "abc123")
    return DynamicDataEngine(memory=memory)


def test_resolve_email(engine: DynamicDataEngine) -> None:
    """{{email}} must be replaced with a valid email-like string."""
    result = engine.resolve("{{email}}")
    assert "@" in result
    assert result != "{{email}}"


def test_resolve_name(engine: DynamicDataEngine) -> None:
    """{{name}} must be replaced with a non-empty string."""
    result = engine.resolve("{{name}}")
    assert len(result) > 0
    assert result != "{{name}}"


def test_resolve_uuid(engine: DynamicDataEngine) -> None:
    """{{uuid}} must produce a UUID-formatted string."""
    result = engine.resolve("{{uuid}}")
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
    assert uuid_pattern.match(result), f"Not a UUID: {result}"


def test_resolve_multiple_in_one_string(engine: DynamicDataEngine) -> None:
    """Multiple placeholders in a single string must all be replaced."""
    template = "User: {{name}}, Email: {{email}}, Company: {{company}}"
    result = engine.resolve(template)
    assert "{{" not in result, f"Unresolved variables remain: {result}"


def test_resolve_extra_dict_priority(engine: DynamicDataEngine) -> None:
    """Extra dict takes highest priority over all other resolution methods."""
    result = engine.resolve("{{name}}", extra={"name": "TestUser"})
    assert result == "TestUser"


def test_resolve_memory_priority(engine_with_memory: DynamicDataEngine) -> None:
    """ContextMemory values are used when extra dict does not have the key."""
    result = engine_with_memory.resolve("{{session_token}}")
    assert result == "abc123"


def test_resolve_env_var(engine: DynamicDataEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables are resolved for unknown variables."""
    monkeypatch.setenv("MY_CUSTOM_VAR", "from_env")
    result = engine.resolve("{{MY_CUSTOM_VAR}}")
    assert result == "from_env"


def test_unresolvable_leaves_placeholder(engine: DynamicDataEngine) -> None:
    """Unknown variables with no resolution source are left unchanged."""
    result = engine.resolve("{{totally_unknown_var_xyz}}")
    assert result == "{{totally_unknown_var_xyz}}"


def test_no_placeholders_unchanged(engine: DynamicDataEngine) -> None:
    """Text without placeholders is returned unchanged."""
    text = "Hello world, no variables here."
    result = engine.resolve(text)
    assert result == text


def test_empty_string(engine: DynamicDataEngine) -> None:
    """Empty string input returns empty string."""
    assert engine.resolve("") == ""


def test_resolve_integer(engine: DynamicDataEngine) -> None:
    """{{integer}} must produce a numeric string."""
    result = engine.resolve("{{integer}}")
    assert result.lstrip("-").isdigit(), f"Not a number: {result}"


def test_resolve_token(engine: DynamicDataEngine) -> None:
    """{{token}} must produce a hex-like string (sha256)."""
    result = engine.resolve("{{token}}")
    assert len(result) >= 32
    assert all(c in "0123456789abcdef" for c in result.lower()), f"Not hex: {result}"


def test_resolve_first_name_last_name(engine: DynamicDataEngine) -> None:
    """{{first_name}} and {{last_name}} must produce non-empty strings."""
    first = engine.resolve("{{first_name}}")
    last = engine.resolve("{{last_name}}")
    assert len(first) > 0
    assert len(last) > 0


def test_faker_map_completeness() -> None:
    """All keys in FAKER_MAP must be non-empty strings."""
    for key, attr in FAKER_MAP.items():
        assert isinstance(key, str) and key
        assert isinstance(attr, str) and attr


def test_memory_with_extraction() -> None:
    """ContextMemory.extract_from_response populates values accessible by engine."""
    memory = ContextMemory()
    memory.extract_from_response(
        {"data": {"id": "user-42", "email": "test@example.com"}},
        {"user_id": "data.id", "user_email": "data.email"},
    )
    engine = DynamicDataEngine(memory=memory)
    assert engine.resolve("{{user_id}}") == "user-42"
    assert engine.resolve("{{user_email}}") == "test@example.com"


def test_extra_overrides_memory() -> None:
    """extra dict must win over ContextMemory for the same key."""
    memory = ContextMemory()
    memory.set("name", "from_memory")
    engine = DynamicDataEngine(memory=memory)
    result = engine.resolve("{{name}}", extra={"name": "from_extra"})
    assert result == "from_extra"


def test_resolve_url(engine: DynamicDataEngine) -> None:
    """{{url}} must produce a URL-like string."""
    result = engine.resolve("{{url}}")
    assert result.startswith("http"), f"Expected URL, got: {result}"


def test_resolve_address(engine: DynamicDataEngine) -> None:
    """{{address}} must produce a non-empty string."""
    result = engine.resolve("{{address}}")
    assert len(result) > 0
    assert result != "{{address}}"
