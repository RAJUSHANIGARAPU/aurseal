"""What GenerationAgent actually emits — the request it sends, and whether it runs.

Reading the generated source for a substring is not enough here: ``body =
{"active": true}`` is valid Python source that raises ``NameError`` the moment
it runs, and a ``body`` variable that is never passed to ``measure_request``
looks identical in the file to one that is. So these tests import the generated
module and call the function, with ``measure_request`` replaced by a recorder.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from api.agents.generation import GenerationAgent
from api.agents.ingestion import PostmanRequest
from api.engine import validation as validation_module


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json"}
    elapsed = MagicMock()
    elapsed.total_seconds.return_value = 0.01
    resp.elapsed = elapsed
    resp.json.return_value = {}
    resp.text = "{}"
    return resp


def _load_generated(path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[dict]]:
    """Import a generated module with ``measure_request`` replaced by a recorder.

    The patch has to be in place before the import: the generated module does
    ``from api.engine.validation import ... measure_request``, which binds the
    function at import time.
    """
    calls: list[dict] = []

    def recorder(method: str, url: str, **kwargs: Any) -> tuple[MagicMock, float]:
        calls.append({"method": method, "url": url, **kwargs})
        return _ok_response(), 12.0

    monkeypatch.setattr(validation_module, "measure_request", recorder)

    spec = importlib.util.spec_from_file_location(f"generated_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def _post_request(**overrides: Any) -> PostmanRequest:
    defaults: dict[str, Any] = {
        "name": "create user",
        "method": "POST",
        "url": "https://api.example.com/users?active=true",
        "headers": {"Content-Type": "application/json"},
        "query_params": {"active": "true"},
        "body": {"active": True, "nickname": None, "age": 30},
        "body_mode": "raw_json",
        "folder_path": ["Users"],
    }
    defaults.update(overrides)
    return PostmanRequest(**defaults)


# --- Defect 1: the request went out empty ---


def test_generated_post_sends_its_body_and_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The emitted call must carry the body and the query parameters."""
    req = _post_request(body={"email": "a@b.c", "age": 30})
    (path,) = GenerationAgent(output_dir=tmp_path).generate([req])

    module, calls = _load_generated(path, monkeypatch)
    module.test_create_user()

    assert len(calls) == 1
    call = calls[0]
    assert call["json"] == {"email": "a@b.c", "age": 30}
    assert call["params"] == {"active": "true"}


def test_generated_get_with_params_does_not_lose_the_query_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The query string is stripped off the URL, so it must be sent as params."""
    req = _post_request(
        name="list users",
        method="GET",
        body=None,
        body_mode="none",
        query_params={"page": "2", "limit": "10"},
        url="https://api.example.com/users?page=2&limit=10",
    )
    (path,) = GenerationAgent(output_dir=tmp_path).generate([req])

    module, calls = _load_generated(path, monkeypatch)
    module.test_list_users()

    assert calls[0]["params"] == {"page": "2", "limit": "10"}
    assert "?" not in calls[0]["url"]


# --- Defect 2: JSON literals emitted into Python source ---


def test_json_literals_are_emitted_as_python(tmp_path: Path) -> None:
    """``true``/``null``/``false`` parse as names and blow up only at runtime."""
    req = _post_request(body={"active": True, "nickname": None, "verified": False})
    (path,) = GenerationAgent(output_dir=tmp_path).generate([req])
    source = path.read_text(encoding="utf-8")

    tree = ast.parse(source)  # passes even for the broken output — hence the eval below
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "body" for t in node.targets)
    ]
    assert assignments, f"no body assignment emitted:\n{source}"
    # ast.literal_eval accepts only real literals — `true` is a Name and raises.
    assert ast.literal_eval(assignments[0].value) == {
        "active": True,
        "nickname": None,
        "verified": False,
    }


def test_generated_module_with_json_typed_body_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The NameError only shows up when the function is called."""
    req = _post_request(body={"active": True, "nickname": None})
    (path,) = GenerationAgent(output_dir=tmp_path).generate([req])

    module, calls = _load_generated(path, monkeypatch)
    module.test_create_user()

    assert calls[0]["json"] == {"active": True, "nickname": None}


# --- Defect 3: colliding module names ---


def test_folders_differing_only_in_case_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """``Users`` and ``users`` slugify to one file; neither test may be lost."""
    post = _post_request(name="create user", folder_path=["Users"])
    get = _post_request(
        name="list users",
        method="GET",
        body=None,
        body_mode="none",
        query_params={},
        url="https://api.example.com/users",
        folder_path=["users"],
    )

    paths = GenerationAgent(output_dir=tmp_path).generate([post, get])

    assert len(set(paths)) == len(paths), f"the same path was returned twice: {paths}"
    written = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    assert "def test_create_user(" in written
    assert "def test_list_users(" in written


def test_colliding_function_names_fail_loudly(tmp_path: Path) -> None:
    """Two items with one name in one module would shadow each other silently."""
    first = _post_request(name="get user", folder_path=["Users"])
    second = _post_request(name="Get User", folder_path=["users"])

    with pytest.raises(ValueError, match="test_get_user"):
        GenerationAgent(output_dir=tmp_path).generate([first, second])


# --- Positive controls ---


def test_a_plain_request_still_generates_a_passing_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generator that wrote nothing would satisfy every failure test above."""
    req = PostmanRequest(
        name="httpbin get",
        method="GET",
        url="https://httpbin.org/get",
        headers={"Accept": "application/json"},
        folder_path=["Basic Requests"],
    )
    (path,) = GenerationAgent(output_dir=tmp_path).generate([req])

    assert path.exists()
    assert path.name == "test_basic_requests.py"

    module, calls = _load_generated(path, monkeypatch)
    module.test_httpbin_get()  # asserts on the validation result internally

    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://httpbin.org/get"
    assert calls[0]["headers"] == {"Accept": "application/json"}


def test_body_free_request_sends_no_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The body fix must not invent a body where the collection has none."""
    req = PostmanRequest(
        name="delete user",
        method="DELETE",
        url="https://api.example.com/users/1",
        folder_path=["Users"],
    )
    (path,) = GenerationAgent(output_dir=tmp_path).generate([req])

    module, calls = _load_generated(path, monkeypatch)
    module.test_delete_user()

    assert calls[0].get("json") is None


def test_generated_source_is_syntax_checked_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing unparseable reaches disk, whatever the model appended."""
    from tests.api_agent._fake_llm import FakeLLM

    llm = FakeLLM(text="assert response.status_code == 200\nassert ((( broken")
    req = _post_request()
    (path,) = GenerationAgent(output_dir=tmp_path, llm=llm).generate([req])

    ast.parse(path.read_text(encoding="utf-8"))
    module, calls = _load_generated(path, monkeypatch)
    module.test_create_user()
    assert calls[0]["json"] == json.loads('{"active": true, "nickname": null, "age": 30}')
