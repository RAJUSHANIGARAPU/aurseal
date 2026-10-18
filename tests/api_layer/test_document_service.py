"""Tests for api.clients.document_service (offline transport)."""

from __future__ import annotations

import json

import pytest

from api.clients.document_service import (
    ApiError,
    DocumentPaths,
    DocumentServiceClient,
    NotAPdf,
)
from api.clients.http_engine import HttpEngine
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

BASE = "https://docs.example.test/api/"
PDF_BYTES = b"%PDF-1.4\n%fake\n%%EOF\n"


def _client(adapter: FakeAdapter, paths: DocumentPaths | None = None) -> DocumentServiceClient:
    return DocumentServiceClient(HttpEngine(BASE, session=session_with(adapter)), paths)


def test_lists_templates_and_their_ids():
    adapter = FakeAdapter().on(
        "GET", "/api/templates", make_response(200, [{"id": "invoice"}, {"id": "letter"}])
    )
    client = _client(adapter)
    assert client.template_ids() == ["invoice", "letter"]


def test_template_source_is_returned_as_bytes():
    adapter = FakeAdapter().on(
        "GET", "/templates/invoice/source", make_response(200, b"Total: {{invoice.total}}")
    )
    assert _client(adapter).template_source("invoice") == b"Total: {{invoice.total}}"


def test_render_posts_template_payload_and_watermark_and_returns_pdf():
    adapter = FakeAdapter().on("POST", "/api/render", make_response(200, PDF_BYTES))
    pdf = _client(adapter).render("invoice", {"invoice": {"number": "ORD-42"}}, watermark="X")
    assert pdf == PDF_BYTES
    sent = adapter.sent[0]
    assert json.loads(sent.body) == {
        "template": "invoice",
        "payload": {"invoice": {"number": "ORD-42"}},
        "watermark": "X",
    }
    assert sent.headers["Accept"] == "application/pdf"


def test_render_without_watermark_sends_none():
    adapter = FakeAdapter().on("POST", "/api/render", make_response(200, PDF_BYTES))
    _client(adapter).render("invoice", {})
    assert "watermark" not in json.loads(adapter.sent[0].body)


def test_render_refuses_a_2xx_body_that_is_not_a_pdf():
    adapter = FakeAdapter().on("POST", "/api/render", make_response(200, b"<html>oops</html>"))
    with pytest.raises(NotAPdf, match="render of 'invoice' is not a PDF"):
        _client(adapter).render("invoice", {})


def test_render_error_raises_api_error():
    adapter = FakeAdapter().on("POST", "/api/render", make_response(422, {"error": "invalid"}))
    with pytest.raises(ApiError):
        _client(adapter).render("invoice", {})


def test_issued_documents_metadata_pdf_and_file():
    adapter = (
        FakeAdapter()
        .on("GET", "/subscriptions/7/documents", make_response(200, [{"id": 3}]))
        .on("GET", "/documents/3.pdf", make_response(200, PDF_BYTES))
        .on("GET", "/documents/3", make_response(200, {"id": 3, "file_id": 9}))
        .on("GET", "/files/9", make_response(200, PDF_BYTES))
    )
    client = _client(adapter)
    assert client.documents_of(7) == [{"id": 3}]
    assert client.document_pdf(3) == PDF_BYTES
    assert client.document(3)["file_id"] == 9
    assert client.file(9) == PDF_BYTES


def test_document_pdf_that_is_not_a_pdf_is_refused():
    adapter = FakeAdapter().on("GET", "/documents/3.pdf", make_response(200, b"{}"))
    with pytest.raises(NotAPdf):
        _client(adapter).document_pdf(3)


def test_paths_are_configurable():
    paths = DocumentPaths(render="documents/render", templates="catalogue")
    adapter = (
        FakeAdapter()
        .on("POST", "/documents/render", make_response(200, PDF_BYTES))
        .on("GET", "/catalogue", make_response(200, [{"key": "a"}]))
    )
    client = _client(adapter, paths)
    assert client.render("a", {}) == PDF_BYTES
    assert client.template_ids(key="key") == ["a"]
