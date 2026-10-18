"""Replays a recorded, language-neutral request sequence (what a curl script in any CI
sends) against the real app: create -> JUnit -> verdicts -> finish -> read back."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).with_name("fixtures") / "curl_sequence.json"


def test_no_sdk_integration_path(http):
    steps = json.loads(FIXTURE.read_text(encoding="utf-8"))
    saved: dict[str, str] = {}
    for step in steps:
        path = step["path"].format(**saved)
        kwargs = {"headers": {"Content-Type": step.get("content_type", "application/json")}}
        if "json" in step:
            kwargs["content"] = json.dumps(step["json"]).encode()
        elif "text" in step:
            kwargs["content"] = step["text"].encode()
        resp = http.request(step["method"], path, **kwargs)
        assert resp.status_code == step["expect_status"], (step["name"], resp.text)
        doc = resp.json()
        for name, key in step.get("save", {}).items():
            saved[name] = doc[key]
        for pointer, value in step.get("expect", {}).items():
            node = doc
            for part in pointer.strip("/").split("/"):
                node = node[int(part)] if isinstance(node, list) else node[part]
            assert node == value, (step["name"], pointer, node)
