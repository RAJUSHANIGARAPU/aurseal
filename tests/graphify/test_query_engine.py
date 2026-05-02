"""Tests for core.graphify.KnowledgeGraphQuery."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.graphify.client import GraphifyClient
from core.graphify.query_engine import KnowledgeGraphQuery


@pytest.fixture()
def tmp_client(tmp_path: Path) -> GraphifyClient:
    return GraphifyClient(project_root=tmp_path)


@pytest.fixture()
def built_client(tmp_client: GraphifyClient) -> GraphifyClient:
    data = {
        "nodes": [{"id": f"mod_{i}"} for i in range(5)],
        "edges": [{"source": "mod_0", "target": "mod_1"}],
    }
    tmp_client.graph_json.write_text(json.dumps(data))
    return tmp_client


# -----------------------------------------------------------------------
# No API key path
# -----------------------------------------------------------------------


def test_raw_query_no_api_key(built_client: GraphifyClient, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch.object(built_client, "query", return_value="cli answer") as mock_q:
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.raw_query("anything?")
    mock_q.assert_called_once_with("anything?")
    assert result == "cli answer"


def test_graph_not_built_returns_message(tmp_client: GraphifyClient, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    qe = KnowledgeGraphQuery(client=tmp_client)
    result = qe.find_test_coverage_gaps()
    assert "not built" in result.lower()


def test_no_api_key_falls_back_to_hint(built_client: GraphifyClient, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Make CLI query fail so we fall through to Claude
    with patch.object(built_client, "query", return_value="graphify query failed: err"):
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.find_test_coverage_gaps()
    assert "ANTHROPIC_API_KEY" in result


# -----------------------------------------------------------------------
# CLI answer short-circuits Claude
# -----------------------------------------------------------------------


def test_cli_answer_used_without_claude(built_client: GraphifyClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with (
        patch.object(built_client, "query", return_value="definitive cli answer") as mock_q,
        patch("core.graphify.query_engine.requests.post") as mock_post,
    ):
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.find_test_coverage_gaps()
    mock_q.assert_called_once()
    mock_post.assert_not_called()
    assert result == "definitive cli answer"


# -----------------------------------------------------------------------
# Claude fallback
# -----------------------------------------------------------------------


def _mock_claude_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"content": [{"text": text}]}
    return resp


def test_claude_fallback_called_when_cli_fails(built_client: GraphifyClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with (
        patch.object(built_client, "query", return_value="graphify query failed: x"),
        patch(
            "core.graphify.query_engine.requests.post",
            return_value=_mock_claude_response("claude says so"),
        ) as mock_post,
    ):
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.find_test_coverage_gaps()
    mock_post.assert_called_once()
    assert result == "claude says so"


def test_explain_module_passes_name(built_client: GraphifyClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with (
        patch.object(built_client, "query", return_value="graphify query failed: x"),
        patch(
            "core.graphify.query_engine.requests.post",
            return_value=_mock_claude_response("explained"),
        ) as mock_post,
    ):
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.explain_module("core.ai.trace_analyzer")
    payload = mock_post.call_args.kwargs["json"]
    prompt_text = payload["messages"][0]["content"]
    assert "core.ai.trace_analyzer" in prompt_text
    assert result == "explained"


def test_locator_impact_passes_selector(built_client: GraphifyClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    selector = "[data-testid='search']"
    with (
        patch.object(built_client, "query", return_value="graphify query failed: x"),
        patch(
            "core.graphify.query_engine.requests.post", return_value=_mock_claude_response("impact")
        ) as mock_post,
    ):
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.locator_impact(selector)
    payload = mock_post.call_args.kwargs["json"]
    assert selector in payload["messages"][0]["content"]
    assert result == "impact"


# -----------------------------------------------------------------------
# dependency_chain delegates to client.shortest_path
# -----------------------------------------------------------------------


def test_dependency_chain_uses_shortest_path(built_client: GraphifyClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with (
        patch.object(built_client, "shortest_path", return_value="A -> B -> C") as mock_sp,
        patch(
            "core.graphify.query_engine.requests.post",
            return_value=_mock_claude_response("chain explained"),
        ),
    ):
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.dependency_chain("A", "C")
    mock_sp.assert_called_once_with("A", "C")
    assert result == "chain explained"


def test_dependency_chain_returns_path_error_directly(built_client: GraphifyClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch.object(built_client, "shortest_path", return_value="graphify path failed: err"):
        qe = KnowledgeGraphQuery(client=built_client)
        result = qe.dependency_chain("X", "Y")
    assert "failed" in result.lower()
