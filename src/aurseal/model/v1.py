"""Result data model, version 1 (the upload contract).

Every document carries ``schema_version``; its major equals this module's major. A field
removal or a meaning change is a new module (``v2``), never an edit here.

Status model: ``passed``, ``failed``, ``broken``, ``skipped``, ``blocked``, ``retest``.
``untested`` is virtual (a planned case without a result) and is never serialised.
Retries are attempts of one record: the record's status is the last attempt's status, and
``flaky_in_run`` is true exactly when an earlier attempt failed or broke and the record
finally passed.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aurseal.model.identity import IDENTITY_RULES_VERSION

SCHEMA_MAJOR = 1
SCHEMA_VERSION = f"{SCHEMA_MAJOR}.0"
_SCHEMA_VERSION_PATTERN = rf"^{SCHEMA_MAJOR}\.\d+$"


class Status(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BROKEN = "broken"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    RETEST = "retest"

    @property
    def is_failure(self) -> bool:
        """Counts as a gate failure (unless the case is quarantined)."""
        return self in (Status.FAILED, Status.BROKEN)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class Attempt(_Model):
    """One execution of a test; retries are further attempts."""

    attempt_no: int = Field(ge=0)
    status: Status
    duration_ms: int | None = Field(default=None, ge=0)
    message: str | None = None
    reason: str | None = None  # e.g. timeout, expected_failure, xfail


class Attachment(_Model):
    name: str
    path: str
    content_type: str | None = None


class ResultTypePayload(_Model):
    """A non-functional result (performance, accessibility, ...) for a registered type."""

    type: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    payload: dict[str, Any]


class TestRecord(_Model):
    """One test's result in one configuration and variant."""

    __test__ = False  # not a pytest test class

    framework: str
    path: str = ""
    suite: str = ""
    name: str
    variant: str | None = None
    configuration: str | None = None
    explicit_id: str | None = None
    fingerprint: str = Field(pattern=r"^[A-Z2-7]{32}$")
    status: Status
    flaky_in_run: bool = False
    attempts: tuple[Attempt, ...] = ()
    duration_ms: int | None = Field(default=None, ge=0)
    message: str | None = None
    reason: str | None = None
    tags: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    result_type: ResultTypePayload | None = None

    @model_validator(mode="after")
    def _attempts_agree(self) -> TestRecord:
        if not self.attempts:
            if self.flaky_in_run:
                raise ValueError("flaky_in_run needs the attempts that show it")
            return self
        numbers = [a.attempt_no for a in self.attempts]
        if numbers != sorted(set(numbers)):
            raise ValueError("attempt numbers must be unique and ascending")
        if self.status is not self.attempts[-1].status:
            raise ValueError("status must be the last attempt's status")
        flaky = self.status is Status.PASSED and any(a.status.is_failure for a in self.attempts)
        if self.flaky_in_run != flaky:
            raise ValueError("flaky_in_run must equal: an attempt failed and the result passed")
        return self

    @property
    def identity(self) -> tuple[str, str | None, str | None]:
        """The result key inside one run: case identity, configuration, variant."""
        return (self.explicit_id or self.fingerprint, self.configuration, self.variant)


class BuildContext(_Model):
    """Where the run came from (CI provider metadata)."""

    provider: str = "local"
    commit: str | None = None
    branch: str | None = None
    pull_request: str | None = None
    pipeline_url: str | None = None
    run_ref: str | None = None


class RunUpload(_Model):
    """The canonical upload document: one run's records plus its context."""

    schema_version: str = Field(default=SCHEMA_VERSION, pattern=_SCHEMA_VERSION_PATTERN)
    identity_rules_version: str = IDENTITY_RULES_VERSION
    project: str | None = None
    name: str | None = None
    started_at: datetime | None = None
    build: BuildContext = Field(default_factory=BuildContext)
    records: tuple[TestRecord, ...] = ()

    def counts(self) -> dict[Status, int]:
        out = dict.fromkeys(Status, 0)
        for record in self.records:
            out[record.status] += 1
        return out


def json_schema() -> dict[str, Any]:
    """JSON Schema of :class:`RunUpload` (draft 2020-12, as pydantic emits it)."""
    schema = RunUpload.model_json_schema()
    schema["$id"] = f"https://aurseal.invalid/schema/run-upload-{SCHEMA_VERSION}.json"
    return schema


def export_json_schema(path: str | Path) -> Path:
    """Write :func:`json_schema` to ``path`` (sorted keys, stable across runs)."""
    target = Path(path)
    target.write_text(json.dumps(json_schema(), indent=2, sort_keys=True) + "\n", "utf-8")
    return target


__all__ = [
    "SCHEMA_VERSION",
    "Attachment",
    "Attempt",
    "BuildContext",
    "ResultTypePayload",
    "RunUpload",
    "Status",
    "TestRecord",
    "export_json_schema",
    "json_schema",
]
