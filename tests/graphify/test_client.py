"""Tests for core.graphify.GraphifyClient."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.graphify.client import BuildResult, GraphifyClient


@pytest.fixture()
def tmp_client(tmp_path: Path) -> GraphifyClient:
    return GraphifyClient(project_root=tmp_path)


# -----------------------------------------------------------------------
# BuildResult
# -----------------------------------------------------------------------


def test_build_result_repr_success():
    r = BuildResult(
        success=True, stdout="ok", stderr="", graph_json=Path("/tmp/graph.json"), graph_html=None
    )
    assert "OK" in repr(r)


def test_build_result_repr_failure():
    r = BuildResult(success=False, stdout="", stderr="err", graph_json=None, graph_html=None)
    assert "FAILED" in repr(r)


# -----------------------------------------------------------------------
# is_built / graph_stats
# -----------------------------------------------------------------------


def test_is_built_false_when_no_graph(tmp_client: GraphifyClient):
    assert tmp_client.is_built() is False


def test_is_built_true_when_graph_exists(tmp_client: GraphifyClient):
    tmp_client.graph_json.write_text('{"nodes":[],"edges":[]}')
    assert tmp_client.is_built() is True


def test_graph_stats_not_built(tmp_client: GraphifyClient):
    assert tmp_client.graph_stats() == {"built": False}


def test_graph_stats_with_graph(tmp_client: GraphifyClient):
    data = {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"source": "a", "target": "b"}]}
    tmp_client.graph_json.write_text(json.dumps(data))
    stats = tmp_client.graph_stats()
    assert stats["built"] is True
    assert stats["nodes"] == 2
    assert stats["edges"] == 1


# -----------------------------------------------------------------------
# load_graph
# -----------------------------------------------------------------------


def test_load_graph_raises_when_missing(tmp_client: GraphifyClient):
    with pytest.raises(FileNotFoundError):
        tmp_client.load_graph()


def test_load_graph_returns_dict(tmp_client: GraphifyClient):
    data = {"nodes": [{"id": "x"}], "edges": []}
    tmp_client.graph_json.write_text(json.dumps(data))
    assert tmp_client.load_graph() == data


# -----------------------------------------------------------------------
# build / update (subprocess mocked)
# -----------------------------------------------------------------------


def _make_proc(returncode=0, stdout="built", stderr=""):
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_build_success(tmp_client: GraphifyClient):
    with patch.object(tmp_client, "_run", return_value=_make_proc()) as mock_run:
        # create graph.json so BuildResult picks it up
        tmp_client.graph_json.write_text("{}")
        result = tmp_client.build()
    assert result.success is True
    cmd = mock_run.call_args[0][0]
    assert "graphify" in cmd[0]


def test_build_deep_mode(tmp_client: GraphifyClient):
    with patch.object(tmp_client, "_run", return_value=_make_proc()) as mock_run:
        tmp_client.build(mode="deep")
    cmd = mock_run.call_args[0][0]
    assert "--mode" in cmd and "deep" in cmd


def test_build_failure(tmp_client: GraphifyClient):
    with patch.object(tmp_client, "_run", return_value=_make_proc(returncode=1, stderr="oops")):
        result = tmp_client.build()
    assert result.success is False
    assert result.stderr == "oops"


def test_update_calls_update_flag(tmp_client: GraphifyClient):
    with patch.object(tmp_client, "_run", return_value=_make_proc()) as mock_run:
        tmp_client.update()
    cmd = mock_run.call_args[0][0]
    assert "--update" in cmd


# -----------------------------------------------------------------------
# query / shortest_path (subprocess mocked)
# -----------------------------------------------------------------------


def test_query_returns_stdout(tmp_client: GraphifyClient):
    with patch.object(tmp_client, "_run", return_value=_make_proc(stdout="  answer  ")):
        assert tmp_client.query("who calls X?") == "answer"


def test_query_returns_error_on_failure(tmp_client: GraphifyClient):
    with patch.object(tmp_client, "_run", return_value=_make_proc(returncode=1, stderr="bad")):
        result = tmp_client.query("foo")
    assert "failed" in result.lower()


def test_shortest_path_returns_stdout(tmp_client: GraphifyClient):
    with patch.object(tmp_client, "_run", return_value=_make_proc(stdout="A -> B -> C")):
        assert tmp_client.shortest_path("A", "C") == "A -> B -> C"
