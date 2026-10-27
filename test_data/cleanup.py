"""Cleanup ledger: everything a test creates is recorded, and discarded at teardown even on failure.

Abandoned drafts, half-built entities and scheduled jobs a test triggered are
not harmless leftovers on a shared environment: an open draft can block every
later change to its entity, and a job that fires after the test has gone can
fail against data nobody is watching. The ledger records each such item with
the callable that discards it, and :meth:`CleanupLedger.discard_all` runs them
newest first. It never raises — a cleanup that fails is logged and reported,
so it is visible without masking the test's own result.

Two ways in:

* explicit — ``ledger.record("order", order_id, lambda: api.delete(...))``;
* observed — attach :meth:`CleanupLedger.observer` to an
  :class:`api.clients.http_engine.HttpEngine` with :class:`CreationRule` s, and
  every successful matching create is recorded without the test doing anything.

* owned - ``ledger.record_owned("message", msg_id, discard, guard)`` for an item
  the system produced for the test (not created by its own call). ``guard``
  re-reads the item and returns a refusal (:func:`test_data.ownership.refusal`)
  or ``None``; it runs right before ``discard``, and a refusal is logged and
  reported instead of deleting.

Only items this ledger recorded are ever touched. One ledger per test
(the ``cleanup_ledger`` fixture in :mod:`test_data.pytest_plugin`), so nothing
leaks between tests or xdist workers.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from api.engine.context_memory import resolve_path

if TYPE_CHECKING:
    from api.clients.http_engine import ApiResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LedgerEntry:
    kind: str
    ident: str
    discard: Callable[[], Any]
    guard: Callable[[], str | None] | None = None


@dataclass(frozen=True)
class CreationRule:
    """Recognises a create in the response stream and says how to discard what it made.

    ``id_path`` is a dotted path into the JSON response body; when it is None
    the id is taken from the ``Location`` header.
    """

    kind: str
    method: str
    url_pattern: str
    discard: Callable[[str], Any]
    id_path: str | None = "id"

    def match(self, method: str, url: str) -> bool:
        return (
            method.upper() == self.method.upper() and re.search(self.url_pattern, url) is not None
        )

    def ident(self, response: ApiResponse) -> str | None:
        if self.id_path is None:
            return response.location_id
        try:
            value = resolve_path(response.json(), self.id_path)
        except (ValueError, KeyError, TypeError, IndexError):
            return None
        return str(value) if value not in (None, "") else None


@dataclass
class CleanupReport:
    discarded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    """Owned items the guard would not let go: left in place, logged with the reason."""

    @property
    def clean(self) -> bool:
        return not self.failed


class CleanupLedger:
    def __init__(self, rules: Iterable[CreationRule] = ()) -> None:
        self._rules = list(rules)
        self._entries: list[LedgerEntry] = []
        self._lock = threading.Lock()

    def record(self, kind: str, ident: str, discard: Callable[[], Any]) -> None:
        """Record one item; recording the same (kind, id) twice keeps one entry."""
        with self._lock:
            if any(e.kind == kind and e.ident == ident for e in self._entries):
                return
            self._entries.append(LedgerEntry(kind, ident, discard))

    def record_owned(
        self,
        kind: str,
        ident: str,
        discard: Callable[[], Any],
        guard: Callable[[], str | None],
    ) -> None:
        """Record a system-produced item; ``guard`` must return ``None`` before it is discarded."""
        with self._lock:
            if any(e.kind == kind and e.ident == ident for e in self._entries):
                return
            self._entries.append(LedgerEntry(kind, ident, discard, guard))

    def forget(self, kind: str, ident: str) -> None:
        """Drop an entry the test already disposed of itself (e.g. it deleted it as the act)."""
        with self._lock:
            self._entries = [e for e in self._entries if (e.kind, e.ident) != (kind, ident)]

    @property
    def entries(self) -> list[LedgerEntry]:
        with self._lock:
            return list(self._entries)

    def observer(self, method: str, url: str, response: ApiResponse) -> None:
        """An :meth:`HttpEngine.observe` callback: records successful creates matching a rule."""
        if not response.is_success:
            return
        for rule in self._rules:
            if not rule.match(method, url):
                continue
            ident = rule.ident(response)
            if ident is None:
                logger.warning("%s created at %s but its id is unreadable", rule.kind, url)
                continue
            self.record(rule.kind, ident, lambda r=rule, i=ident: r.discard(i))

    def discard_all(self) -> CleanupReport:
        """Discard every recorded item, newest first, and forget them all. Never raises."""
        with self._lock:
            entries, self._entries = list(reversed(self._entries)), []
        report = CleanupReport()
        for entry in entries:
            label = f"{entry.kind}:{entry.ident}"
            try:
                if entry.guard is not None:
                    reason = entry.guard()
                    if reason:
                        logger.warning("not discarding %s: %s", label, reason)
                        report.refused.append(f"{label}: {reason}")
                        continue
                outcome = entry.discard()
            except Exception as exc:  # noqa: BLE001 - a cleanup must never mask the test result
                logger.warning("could not discard %s: %s — it may still exist", label, exc)
                report.failed.append(label)
                continue
            status = getattr(outcome, "status", None)
            if isinstance(status, int) and status >= 400 and status != 404:
                logger.warning("discarding %s returned HTTP %d — it may still exist", label, status)
                report.failed.append(label)
            else:
                report.discarded.append(label)
        if entries:
            logger.info(
                "cleanup: %d discarded, %d failed, %d refused by their guard",
                len(report.discarded),
                len(report.failed),
                len(report.refused),
            )
        return report


# ---------------------------------------------------------------------------
# scheduled jobs the test triggered
# ---------------------------------------------------------------------------

PENDING_JOB_STATES = frozenset({"SCHEDULED", "RETRY_PENDING", "PENDING"})


@dataclass
class JobCancellation:
    """What :func:`cancel_pending_jobs` did. ``readable`` is False when listing was refused."""

    readable: bool = True
    cancelled: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def cancel_pending_jobs(
    list_jobs: Callable[[], ApiResponse],
    cancel_job: Callable[[str], ApiResponse],
    *,
    belongs_to_test: Callable[[dict], bool],
    jobs_path: str = "",
    id_field: str = "id",
    state_field: str = "state",
    pending_states: frozenset[str] = PENDING_JOB_STATES,
) -> JobCancellation:
    """Cancel the pending jobs a test triggered, so none fires into its data after it ends.

    Only jobs ``belongs_to_test`` accepts *and* still in a pending state are
    touched; a running or finished job is left alone. A 401/403 on the listing
    is reported as ``readable=False`` once, not as an error per job.
    """
    out = JobCancellation()
    listing = list_jobs()
    if listing.status in (401, 403):
        logger.warning("job listing refused (HTTP %d); pending jobs not cancelled", listing.status)
        out.readable = False
        return out
    if not listing.is_success:
        logger.warning("job listing failed: %s", listing)
        out.readable = False
        return out
    body = listing.json()
    jobs = resolve_path(body, jobs_path) if jobs_path else body
    for job in jobs or []:
        ident = str(job.get(id_field, ""))
        if not belongs_to_test(job):
            continue
        if job.get(state_field) not in pending_states:
            out.skipped.append(ident)
            continue
        try:
            result = cancel_job(ident)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancelling job %s failed: %s", ident, exc)
            out.failed.append(ident)
            continue
        (out.cancelled if result.is_success or result.status == 404 else out.failed).append(ident)
    return out
