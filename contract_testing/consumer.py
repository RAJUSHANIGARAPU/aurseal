"""Consumer contract generation.

Two sources for consumer contracts:

1. From Postman collections (static, no HTTP calls needed):
   Generates contracts from request structure + any stored example responses.

2. From live captures (dynamic, requires capture.py patch active):
   Infers response schemas from actual API responses observed during test execution.

Schema inference uses JSON Schema Draft-07 types: object, array, string, integer,
number, boolean, null. Schemas are intentionally minimal — only the fields actually
observed in the response become required fields. This prevents contracts from being
overly strict and breaking on additive provider changes.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from contract_testing.models import (
    CapturedInteraction,
    Contract,
    Interaction,
    RequestSchema,
    ResponseSchema,
)
from contract_testing.redaction import redact_headers, redact_query

if TYPE_CHECKING:
    from api.agents.ingestion import PostmanRequest

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"/[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
_NUM_SEG_RE = re.compile(r"/\d+")


def normalize_path(url: str) -> str:
    """Strip query params and replace ID segments with {id} for stable keys."""
    path = url.split("?")[0]
    # Extract path from full URL
    if "://" in path:
        from urllib.parse import urlparse

        path = urlparse(path).path
    path = _UUID_RE.sub("/{id}", path)
    path = _NUM_SEG_RE.sub("/{id}", path)
    return path or "/"


def infer_schema(value: Any) -> dict:
    """Recursively infer a JSON Schema v7 schema from a Python value.

    Strategy: observed keys become required, observed types are recorded.
    This produces minimal, stable schemas that tolerate provider additions.
    """
    if value is None:
        return {"type": ["string", "null"]}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if value:
            return {"type": "array", "items": infer_schema(value[0])}
        return {"type": "array"}
    if isinstance(value, dict):
        props = {k: infer_schema(v) for k, v in value.items()}
        return {
            "type": "object",
            "required": list(value.keys()),
            "properties": props,
        }
    return {}


class ConsumerContractGenerator:
    """Generates Contract objects from Postman requests or live captures."""

    def __init__(self, consumer: str, provider: str) -> None:
        self._consumer = consumer
        self._provider = provider

    # ------------------------------------------------------------------
    # From live captures (after test execution)
    # ------------------------------------------------------------------

    def from_captures(self, raw_interactions: list[dict]) -> Contract:
        """Build a Contract from raw captured interaction dicts."""
        interactions = []
        seen_keys: set[str] = set()

        for raw in raw_interactions:
            ci = CapturedInteraction(
                method=raw.get("method", "GET"),
                path=raw.get("path", "/"),
                query=raw.get("query", ""),
                request_headers=raw.get("request_headers", {}),
                request_body=raw.get("request_body"),
                status=raw.get("status", 200),
                response_headers=raw.get("response_headers", {}),
                response_body=raw.get("response_body"),
                test_name=raw.get("test_name", ""),
            )
            interaction = self._captured_to_interaction(ci)
            # Deduplicate by method+path — keep first occurrence only
            if interaction.key not in seen_keys:
                seen_keys.add(interaction.key)
                interactions.append(interaction)

        return Contract(
            consumer=self._consumer,
            provider=self._provider,
            interactions=interactions,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def _captured_to_interaction(self, ci: CapturedInteraction) -> Interaction:
        norm_path = normalize_path(ci.path)
        description = f"{ci.method.upper()} {norm_path}"

        req_body_schema: dict = {}
        if isinstance(ci.request_body, dict):
            req_body_schema = infer_schema(ci.request_body)

        resp_body_schema: dict = {}
        if ci.response_body is not None:
            resp_body_schema = infer_schema(ci.response_body)

        # Applied again here even though captures arrive redacted: this method
        # also serves interactions rebuilt from stored JSON, and the Postman
        # path below never passes through capture at all.
        clean_headers = redact_headers(ci.request_headers)

        return Interaction(
            description=description,
            request=RequestSchema(
                method=ci.method.upper(),
                path=norm_path,
                query=redact_query(ci.query),
                headers=clean_headers,
                body_schema=req_body_schema,
            ),
            response=ResponseSchema(
                status=ci.status,
                headers={
                    k: v for k, v in ci.response_headers.items() if k.lower() in {"content-type"}
                },
                body_schema=resp_body_schema,
            ),
        )

    # ------------------------------------------------------------------
    # From Postman collection (static)
    # ------------------------------------------------------------------

    def from_postman_requests(self, requests: list[PostmanRequest]) -> Contract:
        """Generate a minimal consumer contract from a Postman collection.

        No HTTP calls are made. Schemas cover request structure only;
        response schemas are empty until live captures enrich them.
        """
        interactions = []
        seen: set[str] = set()

        for req in requests:
            path = normalize_path(req.url)
            key = f"{req.method.upper()} {path}"
            if key in seen:
                continue
            seen.add(key)

            body_schema: dict = {}
            if isinstance(req.body, dict) and req.body:
                body_schema = infer_schema(req.body)

            interaction = Interaction(
                description=req.name or key,
                request=RequestSchema(
                    method=req.method.upper(),
                    path=path,
                    headers=redact_headers(req.headers),
                    body_schema=body_schema,
                ),
                response=ResponseSchema(status=200),
            )
            interactions.append(interaction)
            logger.debug("[contract] generated interaction: %s", key)

        return Contract(
            consumer=self._consumer,
            provider=self._provider,
            interactions=interactions,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )
