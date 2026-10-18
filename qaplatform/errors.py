"""Service errors. Each carries the HTTP status an adapter answers with, so the REST layer,
the UI and the Python client map the same failure to the same status everywhere."""

from __future__ import annotations

from typing import Any

PROBLEM_BASE = "https://example.test/problems/"


class PlatformError(Exception):
    status = 500
    slug = "internal"
    title = "Internal error"

    def __init__(self, detail: str = "", *, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors = errors or []

    def to_problem(self, *, instance: str = "", request_id: str = "") -> dict[str, Any]:
        """An RFC 9457 problem document."""
        doc: dict[str, Any] = {
            "type": PROBLEM_BASE + self.slug,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
        }
        if instance:
            doc["instance"] = instance
        if request_id:
            doc["request_id"] = request_id
        if self.errors:
            doc["errors"] = self.errors
        return doc


class BadRequest(PlatformError):
    status, slug, title = 400, "validation", "Request does not match its schema"


class Unauthorized(PlatformError):
    status, slug, title = 401, "unauthorized", "Missing, expired or revoked token"


class Forbidden(PlatformError):
    status, slug, title = 403, "forbidden", "Token lacks the permission"


class NotFound(PlatformError):
    status, slug, title = 404, "not-found", "Not found"


class Conflict(PlatformError):
    status, slug, title = 409, "conflict", "Conflict"


class RunClosed(Conflict):
    slug, title = "run-closed", "Run is finished"


class TooLarge(PlatformError):
    status, slug, title = 413, "too-large", "Body over the limit"


class NotConfigured(PlatformError):
    status, slug, title = 501, "not-configured", "Not configured on this server"


ERRORS_BY_STATUS: dict[int, type[PlatformError]] = {
    400: BadRequest,
    401: Unauthorized,
    403: Forbidden,
    404: NotFound,
    409: Conflict,
    413: TooLarge,
    501: NotConfigured,
}

__all__ = [
    "ERRORS_BY_STATUS",
    "BadRequest",
    "Conflict",
    "Forbidden",
    "NotConfigured",
    "NotFound",
    "PlatformError",
    "RunClosed",
    "TooLarge",
    "Unauthorized",
]
