"""The slash command's coverage report is the CI report; OpenAPI and Postman by URL."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.tools.slash.test_detect_coverage_gaps import ci_step
from tools.slash import coverage_gaps as cg
from traceability import gaps as gaps_mod
from traceability.cli import main as trace_main
from traceability.gaps import GapSourceError, OpenApiSource, PostmanSource

pytest_plugins = ["pytester"]

OPENAPI = {"paths": {"/api/orders": {"get": {}, "post": {}}, "/api/orders/{id}": {"get": {}}}}
SPEC = "## Scenario: Pay by card\n### Tags\n`REQ-2`\n\n## Scenario: Refund\n"
MACHINE = (
    "key: order\ninitial_states: [new]\nstates:\n  new:\n    transitions:\n"
    "      - {action: pay, to: paid}\n      - {action: drop, to: paid}\n  paid: {}\n"
)
TESTS = """
import pytest

STEP = "pay"

@pytest.mark.covers("GET /api/orders")
def test_list():
    pass

@pytest.mark.requirement("REQ-1")
def test_req():
    pass
"""


@pytest.fixture
def project(pytester):
    """A tree laid out like this repository, with the paths the CI steps name."""
    from tests.traceability.conftest import REGISTRY

    pytester.makefile(".yaml", **{"requirements/requirements": REGISTRY})
    pytester.makefile(".json", **{"demo_sut/openapi": json.dumps(OPENAPI)})
    pytester.makefile(".md", **{"specs/checkout": SPEC})
    pytester.makefile(".yaml", **{"demo_sut/config/order.machine": MACHINE})
    pytester.makefile(
        ".json",
        **{"traceability/gap-baseline": json.dumps({"accepted": ["openapi:POST /api/orders"]})},
    )
    pytester.makefile(
        ".json", **{"traceability/workflow-gap-baseline": json.dumps({"accepted": []})}
    )
    pytester.makepyfile(**{"tests/test_tree": TESTS})
    return pytester.path


def _stable(path):
    doc = json.loads(path.read_text())
    doc.pop("generated_at")
    return doc


def test_the_command_report_equals_the_ci_report(project, subprocess_env, capsys):
    ci = project / "ci"
    for step in ("Coverage gaps (ratchet)", "Workflow action gaps (ratchet)"):
        trace_main([*ci_step(step), "--report-only", "--out", str(ci)])
    command = project / "command"
    assert cg.main(["all", "--out", str(command)]) == 0
    for name in ("gaps", "workflow-gaps"):
        assert _stable(command / f"{name}.json") == _stable(ci / f"{name}.json"), name
        assert (command / f"{name}.md").read_text() == (ci / f"{name}.md").read_text(), name
    gaps = json.loads((command / "gaps.json").read_text())
    assert {i["key"] for i in gaps["items"] if i["status"] == "covered"} == {
        "GET /api/orders",
        "REQ-1",
    }
    workflow = (command / "workflow-gaps.md").read_text()
    assert workflow.startswith("GAPS=1 ACTIONS=2 NEW=1 STALE=0") and "order:new:drop" in workflow
    assert "reports:" in capsys.readouterr().out


class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, bytes] = {}

    def do_GET(self):  # noqa: N802 - the http.server hook name
        body = self.routes.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", _Handler.routes
    httpd.shutdown()
    _Handler.routes.clear()


def test_openapi_and_postman_sources_read_a_url(server, monkeypatch):
    base, routes = server
    routes["/openapi.json"] = json.dumps(OPENAPI).encode()
    collection = {"item": [{"name": "List", "request": {"method": "GET", "url": "{{h}}/a/1"}}]}
    routes["/c.json"] = json.dumps(collection).encode()
    routes["/junk"] = b"<html>"
    keys = [i.key for i in OpenApiSource(f"{base}/openapi.json").items()]
    assert keys == ["GET /api/orders", "POST /api/orders", "GET /api/orders/{id}"]
    assert [i.key for i in PostmanSource(f"{base}/c.json").items()] == ["GET /a/1"]
    with pytest.raises(GapSourceError, match="could not be fetched"):
        OpenApiSource(f"{base}/missing").items()
    with pytest.raises(GapSourceError, match="not an OpenAPI document"):
        OpenApiSource(f"{base}/junk").items()
    monkeypatch.setattr(gaps_mod, "MAX_DOC_BYTES", 10)
    with pytest.raises(GapSourceError, match="larger than 10 bytes"):
        OpenApiSource(f"{base}/openapi.json").items()


def test_repeated_source_options_add_up(project, subprocess_env, server, capsys):
    base, routes = server
    routes["/extra.json"] = json.dumps({"paths": {"/api/refunds": {"post": {}}}}).encode()
    out = project / "out"
    rc = cg.main([f"{base}/extra.json", "--out", str(out)])
    assert rc == 0
    doc = json.loads((out / "gaps.json").read_text())
    assert [s["items"] for s in doc["sources"]] == [3, 1, 2, 3]  # openapi x2, spec, reqs
    assert "openapi:POST /api/refunds" in doc["new_gaps"]
    capsys.readouterr()
