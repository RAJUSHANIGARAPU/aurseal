"""Document rendering behind a bearer token: the render lane's checks.

Run by the ``render`` job only (``gate.token_lane``); excluded from the gate. Each
template is rendered with its full payload and the text must carry its title and no
unresolved placeholder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oracles.pdf_text import extract_text
from tests.render_lane.conftest import RenderTarget

pytestmark = pytest.mark.render_check

PAYLOADS = Path(__file__).resolve().parents[1] / "demo_examples" / "documents" / "templates"
PAYLOADS = PAYLOADS / "payloads"


def test_the_token_is_accepted(render_target: RenderTarget):
    response = render_target.client().get("/api/me", headers=render_target.headers())
    assert response.status == 200, response.body[:200]


def test_a_request_without_the_token_is_refused(render_target: RenderTarget):
    assert render_target.client().get("/api/templates").status == 401


def test_every_template_renders_without_placeholders(render_target: RenderTarget):
    client = render_target.client()
    listed = client.get("/api/templates", headers=render_target.headers())
    assert listed.status == 200
    templates = [t["id"] for t in listed.json()]
    assert templates, "the service lists no templates; nothing was checked"
    for template in templates:
        payload_file = PAYLOADS / f"{template}-full.json"
        payload = json.loads(payload_file.read_text()) if payload_file.is_file() else {}
        rendered = client.post(
            "/api/render",
            {"template": template, "payload": payload},
            headers=render_target.headers(),
        )
        assert rendered.status == 200, f"{template}: HTTP {rendered.status}"
        text = extract_text(rendered.body)
        assert "{{" not in text and "}}" not in text, f"{template}: unresolved placeholder"
