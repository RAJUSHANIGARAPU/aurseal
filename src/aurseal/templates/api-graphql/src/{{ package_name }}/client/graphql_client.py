"""One GraphQL client over gql's httpx transport: timeouts, redacted logging, one error type."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx
from gql import Client, GraphQLRequest
from gql.transport.exceptions import TransportQueryError
from gql.transport.httpx import HTTPXTransport
from graphql import DocumentNode, GraphQLSchema

log = logging.getLogger(__name__)

SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key"})


def redact(headers: Mapping[str, str]) -> dict[str, str]:
    """Headers safe to log: credentials replaced by ``***``."""
    return {k: "***" if k.lower() in SENSITIVE_HEADERS else v for k, v in headers.items()}


class GraphQLResponseError(Exception):
    """The server answered, but with ``errors[]``. ``data`` holds any partial result."""

    def __init__(self, errors: list[Any], data: Any = None) -> None:
        self.errors = errors
        self.data = data
        self.messages = [
            str(error.get("message", error)) if isinstance(error, dict) else str(error)
            for error in errors
        ]
        super().__init__("; ".join(self.messages) or "GraphQL errors without a message")


class GraphQLClient:
    """Every operation goes through ``execute``; services never touch gql or httpx directly.

    With ``schema`` set, gql validates each operation locally before sending it, so a
    query that no longer fits the schema fails without a network call. Leave it unset to
    send invalid operations on purpose (error-path tests).
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = 10.0,
        headers: Mapping[str, str] | None = None,
        schema: GraphQLSchema | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        options: dict[str, Any] = {
            "timeout": timeout_s,
            "headers": dict(headers or {}),
            "event_hooks": {"response": [self._log]},
        }
        if transport is not None:
            options["transport"] = transport
        self._client = Client(transport=HTTPXTransport(url=url, **options), schema=schema)

    def execute(
        self,
        document: DocumentNode | str,
        variables: Mapping[str, Any] | None = None,
        *,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        """The ``data`` of the answer; raises ``GraphQLResponseError`` on ``errors[]``.

        HTTP errors without a GraphQL body raise gql's ``TransportServerError`` and
        non-GraphQL answers its ``TransportProtocolError``: both are transport faults,
        not answers, so they are not folded into ``GraphQLResponseError``.
        """
        request = GraphQLRequest(
            document,
            variable_values=dict(variables) if variables is not None else None,
            operation_name=operation_name,
        )
        try:
            return self._client.execute(request)
        except TransportQueryError as error:
            raise GraphQLResponseError(list(error.errors or [str(error)]), error.data) from error

    @staticmethod
    def _log(response: httpx.Response) -> None:
        request = response.request
        log.info(
            "%s %s -> %s headers=%s",
            request.method,
            request.url,
            response.status_code,
            redact(request.headers),
        )
