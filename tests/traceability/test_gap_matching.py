"""Path-template matching and the opt-in endpoint-hit recorder on the HTTP engine."""

from __future__ import annotations

import requests

from api.clients.http_engine import HttpEngine
from traceability.endpoint_hits import (
    EndpointHit,
    iter_matched,
    match_template,
    normalise,
    read_hits,
    record_hit,
)

TEMPLATES = ["/api/orders", "/api/orders/{id}", "/api/orders/me", "/api/orders/{id}/lines/{n}"]


def test_parameters_match_one_segment():
    assert match_template("/api/orders/42", TEMPLATES) == "/api/orders/{id}"
    assert match_template("/api/orders/42/lines/3", TEMPLATES) == "/api/orders/{id}/lines/{n}"
    assert match_template("/api/orders/42/lines", TEMPLATES) is None
    assert match_template("/api/orders/", TEMPLATES) == "/api/orders"


def test_the_most_literal_template_wins():
    assert match_template("/api/orders/me", TEMPLATES) == "/api/orders/me"


def test_normalise_spellings():
    assert normalise("/a/{id}/b/:n/{{c}}/") == "/a/{}/b/{}/{}"


def test_hits_match_by_method():
    hits = [EndpointHit("GET", "/api/orders/7"), EndpointHit("DELETE", "/api/orders/7")]
    got = list(iter_matched(hits, [("GET", "/api/orders/{id}")]))
    assert [k for _, k in got] == ["GET /api/orders/{id}"]


def test_record_and_read(tmp_path):
    path = tmp_path / "h.jsonl"
    record_hit("get", "http://127.0.0.1:9/api/orders/3?x=1", 200, path=path)
    path.open("a").write("not json\n")
    assert read_hits(path) == [EndpointHit("GET", "/api/orders/3", 200, read_hits(path)[0].nodeid)]


class _Session(requests.Session):
    def request(self, method, url, **kw):
        response = requests.Response()
        response.status_code = 204
        response._content = b""
        response.url = url
        return response


def test_the_http_engine_records_only_when_enabled(tmp_path, monkeypatch):
    path = tmp_path / "hits.jsonl"
    HttpEngine("http://127.0.0.1:9/", session=_Session()).get("/api/orders/1")
    assert not path.exists()
    monkeypatch.setenv("ENDPOINT_HITS_FILE", str(path))
    HttpEngine("http://127.0.0.1:9/", session=_Session()).get("/api/orders/1")
    hit = read_hits(path)[0]
    assert (hit.method, hit.path, hit.status) == ("GET", "/api/orders/1", 204)
    assert hit.nodeid.endswith("test_the_http_engine_records_only_when_enabled")
