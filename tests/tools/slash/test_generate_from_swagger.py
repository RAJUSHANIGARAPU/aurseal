"""generate-from-swagger: which cases each operation gets, markers, clients, data file."""

from __future__ import annotations

import ast
import subprocess
import sys

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import openapi_plan as op

DOC = {
    "openapi": "3.0.3",
    "info": {"title": "Shop", "version": "1"},
    "components": {
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "schemas": {
            "Order": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "example": "A-1"},
                    "qty": {"type": "integer"},
                },
            }
        },
    },
    "security": [{"bearer": []}],
    "paths": {
        "/orders": {
            "get": {"tags": ["orders"], "operationId": "listOrders", "responses": {"200": {}}},
            "post": {
                "tags": ["orders"],
                "operationId": "createOrder",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Order"}}
                    },
                },
                "responses": {"201": {}},
            },
        },
        "/orders/{id}": {
            "get": {
                "tags": ["orders"],
                "operationId": "getOrder",
                "parameters": [{"name": "id", "in": "path", "required": True}],
                "responses": {"200": {}},
            }
        },
        "/health": {
            "get": {
                "tags": ["status"],
                "operationId": "health",
                "security": [],
                "responses": {"200": {}},
            }
        },
    },
}


def kinds(ops, op_id):
    return {(c.kind, c.status) for o in ops if o.op_id == op_id for c in o.cases}


def test_two_tags_make_two_modules_and_every_operation_has_a_success_test(tmp_path):
    files = op.write(DOC, "shop", tmp_path, client_dir=tmp_path / "none")
    modules = sorted(p.name for p in files if p.name.startswith("test_"))
    assert modules == ["test_shop_orders_api.py", "test_shop_status_api.py"]
    names = set()
    for path in files:
        if path.suffix == ".py":
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names |= {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for op_id in ("listorders", "createorder", "getorder", "health"):
        assert f"test_{op_id}_success" in names


def test_failure_cases_only_where_the_operation_can_return_them():
    ops = op.plan(DOC)
    assert kinds(ops, "createorder") == {
        ("success", 201),
        ("missing_required", 400),
        ("unauthorised", 401),
    }
    assert kinds(ops, "getorder") == {("success", 200), ("unauthorised", 401), ("not_found", 404)}
    assert kinds(ops, "health") == {("success", 200)}  # public: no 401, nothing required: no 400


def test_every_test_carries_both_markers_and_the_batch_selects_them(tmp_path):
    op.write(DOC, "shop", tmp_path / "gen", client_dir=tmp_path / "none")
    for module in (tmp_path / "gen" / "shop").glob("test_*.py"):
        text = module.read_text(encoding="utf-8")
        assert 'pytestmark = [pytest.mark.api, pytest.mark.generated_batch(name="shop")]' in text
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    api\n    generated_batch(name)\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            "-m",
            "generated_batch(name='shop')",
            str(tmp_path / "gen"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert "9 tests collected" in result.stdout, result.stdout + result.stderr
    other = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            "-m",
            "generated_batch(name='other')",
            str(tmp_path / "gen"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert "9 deselected" in other.stdout, other.stdout


def test_existing_clients_are_reused_missing_ones_created(tmp_path):
    clients = tmp_path / "clients"
    clients.mkdir()
    (clients / "orders_client.py").write_text("class OrdersClient:\n    pass\n", encoding="utf-8")
    files = op.write(DOC, "shop", tmp_path / "gen", client_dir=clients)
    names = {p.name for p in files}
    assert "orders_client.py" not in names and "status_client.py" in names
    orders = (tmp_path / "gen" / "shop" / "test_shop_orders_api.py").read_text(encoding="utf-8")
    assert "from api.clients.orders_client import OrdersClient" in orders


def test_example_bodies_go_to_the_feature_data_file(tmp_path):
    files = op.write(DOC, "shop", tmp_path, client_dir=tmp_path / "none")
    data = next(p for p in files if p.name == "shop_api_data.json").read_text(encoding="utf-8")
    assert '"createorder"' in data and '"A-1"' in data
    for path in files:
        if path.suffix == ".py":
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_summary():
    text = op.summary(DOC)
    assert "Title: Shop" in text and "Paths: 3" in text and "bearer" in text


def test_the_command_is_manual_only_and_commits_only_after_three_passes():
    meta, body = read_command("generate-from-swagger")
    assert meta["disable-model-invocation"] is True
    assert claude_md_lists("generate-from-swagger")
    for promise in (
        "3 consecutive passes",
        "at most 5 fix rounds",
        "nothing is committed",
        "reused, never duplicated",
        "generated_batch",
    ):
        assert promise in body, promise
