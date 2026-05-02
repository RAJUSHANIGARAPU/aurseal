"""Data models for the Contract Testing Layer.

Contract format is compatible with Pact Specification v2.0.0 so contracts
can be uploaded to a Pact Broker if one is available.

Schema format is JSON Schema Draft-07 (subset). Inferred schemas use only
`type`, `required`, and `properties` to keep contracts readable and stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ChangeType(str, Enum):
    BREAKING = "breaking"
    NON_BREAKING = "non_breaking"
    NONE = "none"


@dataclass
class RequestSchema:
    method: str
    path: str
    query: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {"method": self.method.upper(), "path": self.path}
        if self.query:
            d["query"] = self.query
        if self.headers:
            d["headers"] = self.headers
        if self.body_schema:
            d["bodySchema"] = self.body_schema
        return d

    @classmethod
    def from_dict(cls, data: dict) -> RequestSchema:
        return cls(
            method=data.get("method", "GET"),
            path=data.get("path", "/"),
            query=data.get("query", ""),
            headers=data.get("headers", {}),
            body_schema=data.get("bodySchema", {}),
        )


@dataclass
class ResponseSchema:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {"status": self.status}
        if self.headers:
            d["headers"] = self.headers
        if self.body_schema:
            d["bodySchema"] = self.body_schema
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ResponseSchema:
        return cls(
            status=data.get("status", 200),
            headers=data.get("headers", {}),
            body_schema=data.get("bodySchema", {}),
        )


@dataclass
class Interaction:
    """One recorded request/response pair — the unit of a consumer contract."""

    description: str
    request: RequestSchema
    response: ResponseSchema
    provider_states: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "providerStates": self.provider_states,
            "request": self.request.to_dict(),
            "response": self.response.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Interaction:
        return cls(
            description=data.get("description", ""),
            request=RequestSchema.from_dict(data.get("request", {})),
            response=ResponseSchema.from_dict(data.get("response", {})),
            provider_states=data.get("providerStates", []),
        )

    @property
    def key(self) -> str:
        """Stable identifier for matching interactions across contract versions."""
        return f"{self.request.method.upper()} {self.request.path}"


@dataclass
class Contract:
    """A versioned contract between one consumer and one provider.

    Serialises to Pact v2 compatible JSON.
    """

    consumer: str
    provider: str
    interactions: list[Interaction]
    version: str = "1.0.0"
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "consumer": {"name": self.consumer},
            "provider": {"name": self.provider},
            "interactions": [i.to_dict() for i in self.interactions],
            "metadata": {
                "pactSpecification": {"version": "2.0.0"},
                "contract_version": self.version,
                "created_at": self.created_at,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> Contract:
        meta = data.get("metadata", {})
        return cls(
            consumer=data.get("consumer", {}).get("name", "unknown"),
            provider=data.get("provider", {}).get("name", "unknown"),
            interactions=[Interaction.from_dict(i) for i in data.get("interactions", [])],
            version=meta.get("contract_version", "1.0.0"),
            created_at=meta.get("created_at", ""),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> Contract:
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_file(cls, path: Path) -> Contract:
        return cls.from_json(path.read_text(encoding="utf-8"))


@dataclass
class ValidationResult:
    """Result of validating one interaction against a live response."""

    interaction_key: str
    passed: bool
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


@dataclass
class ContractDiff:
    """Breaking and non-breaking changes between two contract versions."""

    breaking: list[str] = field(default_factory=list)
    non_breaking: list[str] = field(default_factory=list)

    @property
    def has_breaking(self) -> bool:
        return bool(self.breaking)

    @property
    def change_type(self) -> ChangeType:
        if self.breaking:
            return ChangeType.BREAKING
        if self.non_breaking:
            return ChangeType.NON_BREAKING
        return ChangeType.NONE

    def next_version(self, current: str) -> str:
        """Compute the next semver version based on change severity."""
        parts = current.split(".")
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        if self.has_breaking:
            return f"{major + 1}.0.0"
        if self.non_breaking:
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"


@dataclass
class CapturedInteraction:
    """Raw HTTP interaction captured during test execution."""

    method: str
    path: str
    query: str
    request_headers: dict[str, str]
    request_body: Any
    status: int
    response_headers: dict[str, str]
    response_body: Any
    test_name: str = ""
