"""generate-from-postman: the fallback planner and what the command promises."""

from __future__ import annotations

import ast
import json

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import postman_plan as pp
from tools.slash import reach

TOKEN = "s3cr3t-T0ken-value-0123456789abcdef"
PASSWORD = "hunter2-correct-horse"


def request(name, method="GET", path=("items",), tests=(), headers=(), body=None, auth=None):
    req = {"method": method, "url": {"raw": "{{baseUrl}}/x", "path": list(path)}}
    req["header"] = [{"key": k, "value": v} for k, v in headers]
    if body is not None:
        req["body"] = {"mode": "raw", "raw": json.dumps(body)}
    if auth:
        req["auth"] = auth
    item = {"name": name, "request": req}
    if tests:
        item["event"] = [{"listen": "test", "script": {"exec": list(tests)}}]
    return item


COLLECTION = {
    "info": {"name": "Shop API", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/"},
    "variable": [
        {"key": "baseUrl", "value": "https://shop.example.test"},
        {"key": "apiKey", "value": TOKEN},
    ],
    "item": [
        {
            "name": "Orders",
            "item": [
                request("List orders", tests=["pm.response.to.have.status(200);"]),
                request(
                    "Create order",
                    "POST",
                    tests=["pm.expect(pm.response.code).to.eql(201);"],
                    body={"sku": "A-1", "password": PASSWORD},
                ),
                request(
                    "Get order",
                    path=("orders", ":id"),
                    headers=[("Authorization", f"Bearer {TOKEN}"), ("Accept", "application/json")],
                    tests=["pm.response.to.be.json;"],
                ),
            ],
        },
        {
            "name": "Customers",
            "item": [
                request("List customers", tests=["pm.expect(jsonData.items).to.have.lengthOf(3);"]),
                request("Get customer", tests=["pm.expect(jsonData.name).to.eql('Kim');"]),
                request(
                    "Ping",
                    headers=[("X-Api-Key", "{{apiKey}}")],
                    auth={"type": "bearer", "bearer": [{"key": "token", "value": TOKEN}]},
                    tests=["pm.response.to.have.header('X-Request-Id');", "pm.sendRequest('x')"],
                ),
            ],
        },
    ],
}


def written(tmp_path):
    src = tmp_path / "collection.json"
    src.write_text(json.dumps(COLLECTION), encoding="utf-8")
    return pp.write(src, tmp_path / "out", client_dir=tmp_path / "no-clients")


def test_two_folders_of_three_requests_make_two_modules_of_three_tests(tmp_path):
    files = written(tmp_path)
    modules = sorted(p for p in files if p.name.startswith("test_"))
    assert [p.name for p in modules] == ["test_customers_api.py", "test_orders_api.py"]
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        tests = [
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
        ]
        assert len(tests) == 3
    for path in files:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_no_secret_from_the_collection_reaches_generated_code(tmp_path):
    for path in written(tmp_path):
        text = path.read_text(encoding="utf-8")
        assert TOKEN not in text and PASSWORD not in text, path.name


def test_each_kind_of_check_is_translated():
    assert pp.translate("pm.response.to.have.status(200);") == [
        "assert response.status_code == 200"
    ]
    assert pp.translate("pm.expect(pm.response.code).to.eql(201)") == [
        "assert response.status_code == 201"
    ]
    assert pp.translate("pm.response.to.be.json;") == [
        'assert "application/json" in response.headers.get("Content-Type", "")'
    ]
    assert pp.translate("pm.expect(jsonData.user.name).to.eql('Kim')") == [
        'assert body["user"]["name"] == \'Kim\''
    ]
    assert pp.translate("pm.expect(jsonData.items).to.have.lengthOf(3)") == [
        'assert len(body["items"]) == 3'
    ]
    assert pp.translate("pm.expect(jsonData.items.length).to.eql(2)") == [
        'assert len(body["items"]) == 2'
    ]
    assert pp.translate("pm.response.to.have.header('X-Request-Id')") == [
        'assert "X-Request-Id" in response.headers'
    ]
    assert pp.translate("pm.sendRequest('x')") == []


def test_untranslated_checks_are_noted_for_review(tmp_path):
    text = next(p for p in written(tmp_path) if p.name == "test_customers_api.py").read_text()
    assert "were not translated" in text
    assert "@pytest.fixture" in text and "pytestmark = pytest.mark.api" in text


def test_an_existing_client_with_every_method_is_reused(tmp_path):
    clients = tmp_path / "clients"
    clients.mkdir()
    (clients / "orders_client.py").write_text(
        "class OrdersClient:\n"
        "    def list_orders(self): ...\n"
        "    def create_order(self): ...\n"
        "    def get_order(self): ...\n",
        encoding="utf-8",
    )
    _, groups = pp.parse(COLLECTION)
    assert pp.existing_client(groups[0], clients) == "api.clients.orders_client"
    assert pp.existing_client(groups[1], clients) is None


def test_the_summary_previews_requests():
    text = pp.summary(COLLECTION)
    assert "Collection: Shop API" in text and "Top-level items: 2" in text
    assert "- List orders: GET /items" in text


def test_an_unreachable_base_url_is_reported_down():
    ok, detail = reach.probe("http://127.0.0.1:9/", timeout=2)
    assert not ok and "unreachable" in detail


def test_the_command_is_manual_only_and_states_its_rules():
    meta, body = read_command("generate-from-postman")
    assert meta["disable-model-invocation"] is True
    assert claude_md_lists("generate-from-postman")
    assert body.index("tools.slash.reach") < body.index("pytest generated_tests")
    for promise in (
        "3 consecutive passes",
        "at most 5 fix rounds",
        "nothing is committed",
        "Credentials are never copied",
        "generated_tests/",
    ):
        assert promise in body, promise
