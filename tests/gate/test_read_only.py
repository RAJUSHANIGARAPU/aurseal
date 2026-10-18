"""ReadOnlyClient keeps writes out of reach; the source scan keeps it that way."""

from __future__ import annotations

from pathlib import Path

import pytest

import gate.preflight
from gate.read_only import ReadOnlyClient, find_write_calls

GATE_DIR = Path(gate.preflight.__file__).parent


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return "body"

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url, kwargs))

    def post(self, url, **kwargs):  # pragma: no cover - must be unreachable
        raise AssertionError("write reached")


def test_only_reads_are_reachable():
    session = _Session()
    client = ReadOnlyClient.over(session, "https://api.example.test/v1/", headers={"A": "1"})
    assert client.get("/orders/1", headers={"B": "2"}) == "body"
    client.head("orders/1")
    assert session.calls[0] == (
        "GET",
        "https://api.example.test/v1/orders/1",
        {"headers": {"A": "1", "B": "2"}},
    )
    assert session.calls[1][:2] == ("HEAD", "https://api.example.test/v1/orders/1")
    for name in ("post", "put", "patch", "delete", "session", "_session"):
        assert not hasattr(client, name)


def test_client_is_immutable_and_slot_only():
    client = ReadOnlyClient(lambda url, **kw: url)
    with pytest.raises(AttributeError):
        client.post = lambda *a: None  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        client.base_url = "https://elsewhere.test"
    assert not hasattr(client, "__dict__")


def test_absolute_urls_pass_through_and_head_is_optional():
    client = ReadOnlyClient(lambda url, **kw: url, "https://api.example.test")
    assert client.get("https://other.example.test/x") == "https://other.example.test/x"
    with pytest.raises(NotImplementedError):
        client.head("x")


def test_find_write_calls_flags_writes(tmp_path):
    source = tmp_path / "scan.py"
    source.write_text(
        "def f(s, c):\n"
        "    s.get('/a')\n"
        "    s.post('/a')\n"
        "    c.session.delete('/b')\n"
        "    s.request('PATCH', '/c')\n"
        "    s.request('GET', '/d')\n"
    )
    hits = find_write_calls([tmp_path])
    assert [(h.line, h.call) for h in hits] == [
        (3, "s.post"),
        (4, "c.session.delete"),
        (5, "s.request('PATCH', '/c')"),
    ]
    assert str(hits[0]).endswith("scan.py:3: s.post")


def test_the_preflight_module_cannot_write():
    assert find_write_calls([GATE_DIR / "preflight.py", GATE_DIR / "read_only.py"]) == []
