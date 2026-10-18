"""Frozen record types: the same shapes as the run-bundle records.

``to_json()`` gives a plain dict (lists, not tuples; ``None`` fields kept so a bundle
line is self-describing). ``from_json()`` ignores keys it does not know, so a newer
writer's extra fields never break an older reader.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, TypeVar

T = TypeVar("T", bound="_Record")

DEFAULT_PROJECT = "default"

OUTCOMES = ("passed", "failed", "error", "xfailed", "skipped", "not_run")
EVIDENCE_KINDS = (
    "trace",
    "video",
    "screenshot",
    "failure_bundle",
    "har",
    "log",
    "allure",
    "report",
    "other",
)
RUN_SOURCES = (
    "pytest",
    "junit-import",
    "api",
    "bundle",
    "automated",
    "manual",
    "exploratory",
    "import",
)
RUN_STATUSES = ("running", "finished", "interrupted")
INTEGRITY_VERDICTS = ("PASS", "FAIL", "UNJUDGED")


class _Record:
    _tuple_fields: ClassVar[tuple[str, ...]] = ()

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)  # type: ignore[call-overload]
        for name in self._tuple_fields:
            value = data.get(name)
            if value is not None:
                data[name] = [list(v) if isinstance(v, tuple) else v for v in value]
        return data

    @classmethod
    def from_json(cls: type[T], data: dict[str, Any]) -> T:
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        kwargs = {k: v for k, v in data.items() if k in known}
        for name in cls._tuple_fields:
            if kwargs.get(name) is not None:
                kwargs[name] = tuple(tuple(v) if isinstance(v, list) else v for v in kwargs[name])
        return cls(**kwargs)


@dataclass(frozen=True)
class RunRecord(_Record):
    run_id: str
    project_id: str = DEFAULT_PROJECT
    source: str = "pytest"
    external_id: str | None = None
    name: str | None = None
    framework: str | None = None
    lane: str | None = None
    environment: str | None = None
    sut_version: str | None = None
    git_sha: str | None = None
    git_branch: str | None = None
    ci_provider: str | None = None
    ci_url: str | None = None
    trigger: str | None = None
    schedule_name: str | None = None
    run_request_id: str | None = None
    release_label: str | None = None
    expected_count: int | None = None
    host: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    status: str = "running"
    integrity_verdict: str | None = None
    provenance: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class InvocationRecord(_Record):
    run_id: str
    seq: int
    project_id: str = DEFAULT_PROJECT
    started_at: str | None = None
    finished_at: str | None = None
    exit_status: int | None = None
    argv: tuple[str, ...] = ()
    worker_count: int | None = None
    lane: str | None = None  # one run id may span lanes (one CI job per lane)

    _tuple_fields: ClassVar[tuple[str, ...]] = ("argv",)


@dataclass(frozen=True)
class CaseRecord(_Record):
    case_id: str
    case_key: str
    project_id: str = DEFAULT_PROJECT
    framework: str = "pytest"
    title: str | None = None
    module: str | None = None
    qualname: str | None = None
    file_path: str | None = None
    line: int | None = None
    description: str | None = None
    owner: str | None = None
    priority: str | None = None
    # ({"name": "requirement", "args": ["REQ-12"]}, ...)
    markers: tuple[dict[str, Any], ...] = ()
    steps: tuple[str, ...] = ()
    # (("requirement", "REQ-12"), ("feature", "billing"), ...)
    links: tuple[tuple[str, str], ...] = ()
    status: str = "active"
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    current_revision_id: str | None = None

    _tuple_fields: ClassVar[tuple[str, ...]] = ("markers", "steps", "links")

    @property
    def marker_names(self) -> tuple[str, ...]:
        return tuple(m.get("name", "") for m in self.markers)


@dataclass(frozen=True)
class RevisionRecord(_Record):
    revision_id: str
    case_id: str
    project_id: str = DEFAULT_PROJECT
    source_hash: str | None = None
    file_blob_sha: str | None = None
    git_sha: str | None = None
    steps: tuple[str, ...] = ()
    first_seen_run_id: str | None = None
    created_at: str | None = None

    _tuple_fields: ClassVar[tuple[str, ...]] = ("steps",)


@dataclass(frozen=True)
class ResultRecord(_Record):
    run_id: str
    nodeid: str
    outcome: str
    attempt: int = 1
    project_id: str = DEFAULT_PROJECT
    invocation: int | None = None  # the invocation's seq within the run
    case_id: str | None = None
    revision_id: str | None = None
    param_id: str | None = None
    is_cleared: bool = False
    duration_ms: int | None = None
    setup_ms: int | None = None
    call_ms: int | None = None
    teardown_ms: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    worker: str | None = None
    browser: str | None = None
    shard_index: int | None = None
    message: str | None = None
    longrepr: str | None = None
    failure_fingerprint: str | None = None
    skip_reason: str | None = None
    defect_ref: str | None = None
    markers: tuple[str, ...] = ()

    _tuple_fields: ClassVar[tuple[str, ...]] = ("markers",)


@dataclass(frozen=True)
class EvidenceRecord(_Record):
    run_id: str
    kind: str
    uri: str
    nodeid: str | None = None  # None: run-level evidence (an Allure directory, a log)
    attempt: int | None = None  # None: the test's final attempt
    project_id: str = DEFAULT_PROJECT
    sha256: str | None = None
    bytes: int | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class VerdictRecord(_Record):
    """One ``verification/verdicts.py`` row, as stored."""

    run_id: str
    nodeid: str
    kind: str
    verdict: str
    ticket: str | None = None
    detail: str = ""
    subject: str | None = None
    recorded_at: str | None = None
    project_id: str = DEFAULT_PROJECT

    @classmethod
    def from_row(cls, row: dict[str, Any], run_id: str | None = None) -> VerdictRecord:
        """From a ``verdicts.jsonl`` line (``timestamp`` becomes ``recorded_at``)."""
        return cls(
            run_id=run_id or str(row.get("run_id") or ""),
            nodeid=str(row.get("nodeid") or row.get("test_id") or ""),
            kind=str(row.get("kind") or ""),
            verdict=str(row.get("verdict") or ""),
            ticket=row.get("ticket") or None,
            detail=str(row.get("detail") or ""),
            subject=row.get("subject") or None,
            recorded_at=row.get("recorded_at") or row.get("timestamp") or None,
        )


@dataclass(frozen=True)
class ProvenanceRecord(_Record):
    """One ``gate/provenance.py`` data-origin row, as stored."""

    run_id: str
    nodeid: str
    origin: str
    fixture: str = ""
    entity_id: str | None = None
    candidates: int = 1
    attributes: dict[str, Any] = field(default_factory=dict)
    recorded_at: str | None = None
    project_id: str = DEFAULT_PROJECT


__all__ = [
    "DEFAULT_PROJECT",
    "EVIDENCE_KINDS",
    "INTEGRITY_VERDICTS",
    "OUTCOMES",
    "RUN_SOURCES",
    "RUN_STATUSES",
    "CaseRecord",
    "EvidenceRecord",
    "InvocationRecord",
    "ProvenanceRecord",
    "ResultRecord",
    "RevisionRecord",
    "RunRecord",
    "VerdictRecord",
]
