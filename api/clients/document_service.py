"""A thin client for a document-render service: templates, render, stored documents and files.

Document pipelines are judged on the artifact the customer receives, so a test
needs three things from the service and nothing more: the template estate
(which templates are deployed, and their source for structural checks), a
direct render of a template with a payload, and the bytes of a document the
system issued on its own. Every call goes through
:class:`api.clients.http_engine.HttpEngine`, so auth, run tagging and response
observers apply unchanged.

Paths are relative to the engine's base URL and configurable through
:class:`DocumentPaths`, because render services disagree on naming (``/render``
vs ``/documents/render``) while the shape of the conversation is the same::

    docs = DocumentServiceClient(HttpEngine("https://docs.example.test/api/"))
    pdf = docs.render("invoice", {"invoice": {"number": "ORD-42"}})
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api.clients.http_engine import ApiError, HttpEngine
from oracles.pdf_text_view import NotAPdf, is_pdf

PDF = "application/pdf"


@dataclass(frozen=True)
class DocumentPaths:
    """Endpoint paths, relative to the engine base URL. ``{id}`` is substituted."""

    templates: str = "templates"
    template_source: str = "templates/{id}/source"
    render: str = "render"
    document: str = "documents/{id}"
    document_pdf: str = "documents/{id}.pdf"
    file: str = "files/{id}"
    owner_documents: str = "subscriptions/{id}/documents"


class DocumentServiceClient:
    """List templates, read template source, render to PDF and download stored documents."""

    def __init__(
        self,
        engine: HttpEngine,
        paths: DocumentPaths | None = None,
        *,
        store_timeout_s: float = 30.0,
        store_interval_s: float = 1.0,
    ) -> None:
        self.engine = engine
        self.paths = paths or DocumentPaths()
        # A file id can be listed before the file reaches storage: file() polls on 404.
        self.store_timeout_s = store_timeout_s
        self.store_interval_s = store_interval_s

    # -- templates ----------------------------------------------------------

    def templates(self) -> list[dict[str, Any]]:
        """The deployed templates as the service lists them."""
        return self.engine.get(self.paths.templates).raise_for_status().json()

    def template_ids(self, key: str = "id") -> list[str]:
        return [str(t[key]) for t in self.templates()]

    def template_source(self, template_id: str) -> bytes:
        """The template's source bytes (text or an OOXML package)."""
        path = self.paths.template_source.format(id=template_id)
        return self.engine.get_bytes(path, accept="*/*")

    # -- rendering ----------------------------------------------------------

    def render(
        self, template: str, payload: Mapping[str, Any], *, watermark: str | None = None
    ) -> bytes:
        """Render ``template`` with ``payload``; returns PDF bytes.

        Raises :class:`ApiError` on a non-2xx answer and :class:`NotAPdf` when a
        2xx body is not a PDF (an error page served with 200 is not a document).
        """
        body: dict[str, Any] = {"template": template, "payload": dict(payload)}
        if watermark:
            body["watermark"] = watermark
        response = self.engine.post(
            self.paths.render, json=body, headers={"Accept": PDF}
        ).raise_for_status()
        return _require_pdf(response.content, f"render of {template!r}")

    # -- issued documents ---------------------------------------------------

    def documents_of(self, owner_id: Any) -> list[dict[str, Any]]:
        """Metadata of every document issued for one owner entity."""
        path = self.paths.owner_documents.format(id=owner_id)
        return self.engine.get(path).raise_for_status().json()

    def document(self, document_id: Any) -> dict[str, Any]:
        path = self.paths.document.format(id=document_id)
        return self.engine.get(path).raise_for_status().json()

    def document_pdf(self, document_id: Any) -> bytes:
        path = self.paths.document_pdf.format(id=document_id)
        return _require_pdf(self.engine.get_bytes(path, accept=PDF), f"document {document_id}")

    def file(self, file_id: Any, *, timeout_s: float | None = None) -> bytes:
        """Raw bytes of a stored file, waiting up to ``timeout_s`` for it to be stored.

        Only a 404 is waited on; any other error raises at once. ``timeout_s=0`` asks once.
        """
        return self.engine.get_bytes_once_stored(
            self.paths.file.format(id=file_id),
            timeout_s=self.store_timeout_s if timeout_s is None else timeout_s,
            interval_s=self.store_interval_s,
        )


def _require_pdf(data: bytes, what: str) -> bytes:
    if not is_pdf(data):
        head = data[:40].decode("latin-1", errors="replace")
        raise NotAPdf(f"{what} is not a PDF ({len(data)} bytes, starts with {head!r})")
    return data


__all__ = ["ApiError", "DocumentPaths", "DocumentServiceClient", "NotAPdf"]
