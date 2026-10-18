"""Hand each allowlisted entity to at most one test per run, across every xdist worker.

Under ``SEED_MODE=targets`` several tests draw from one allowlist. Two mutating
tests on the same entity would each assert against a state the other changed,
so an entity is claimed once per run. A per-process singleton is not enough
under ``pytest -n auto``: every worker is its own process. The claim ledger is
therefore a directory of claim files under ``reports/entity-allowlist/<run id>/``,
each created with ``O_CREAT | O_EXCL``. The filesystem guarantees exactly one
creator wins, on every worker, without a lock server.

An entity that failed a safety preflight is set aside for the rest of the run
(a rejection file), so later tests do not read it again. Every claim and
rejection is appended to ``reports/logs/entity-allowlist.log``, so "which test
touched ORD-42?" has an answer after the run.

:func:`resolve_targets` maps allowlisted identifiers to the reference the API
needs (for example a current version id) through an injected paged listing.
:func:`claim_for` puts it together: the first allowlisted, unclaimed entity
that resolves and passes the caller's preflight, or an explanation per entity
of why none qualified.

Configuration: ``ENTITY_ALLOWLIST_MAX_PAGES`` (default 20) caps the listing pages
read; the run id comes from :mod:`core.run_id` (``TEST_RUN_ID``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core import run_id as run_ids

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_ROOT = Path("reports") / "entity-allowlist"
DEFAULT_LOG = Path("reports") / "logs" / "entity-allowlist.log"
PAGE_LIMIT_KEY = "ENTITY_ALLOWLIST_MAX_PAGES"
DEFAULT_MAX_PAGES = 20

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _file_name(ident: str) -> str:
    """A filesystem-safe, collision-free name for an identifier."""
    readable = _UNSAFE_CHARS.sub("_", ident)[:48]
    digest = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{digest}.json"


class TargetPicker:
    """Run-wide claim ledger. Safe across threads and processes sharing one filesystem."""

    def __init__(
        self,
        run_id: str | None = None,
        *,
        ledger_root: Path | str = DEFAULT_LEDGER_ROOT,
        log_path: Path | str | None = DEFAULT_LOG,
    ) -> None:
        self.run_id = run_ids.sanitize(run_id) if run_id else run_ids.run_id()
        self.directory = Path(ledger_root) / self.run_id
        self._claims = self.directory / "claims"
        self._rejections = self.directory / "rejected"
        self._claims.mkdir(parents=True, exist_ok=True)
        self._rejections.mkdir(parents=True, exist_ok=True)
        self._log = Path(log_path) if log_path else None

    # -- queries ------------------------------------------------------------

    def is_available(self, ident: str) -> bool:
        name = _file_name(ident)
        return not (self._claims / name).exists() and not (self._rejections / name).exists()

    def claims(self) -> dict[str, str]:
        """identifier -> test that claimed it."""
        return {rec["ident"]: rec["test"] for rec in self._read_all(self._claims)}

    def rejections(self) -> dict[str, str]:
        """identifier -> reason it was set aside."""
        return {rec["ident"]: rec["reason"] for rec in self._read_all(self._rejections)}

    def status(self, allowlist: Iterable[str], notes: Mapping[str, str] | None = None) -> str:
        """One clause per allowlisted identifier, for the message a skipped test carries."""
        claims, rejections, notes = self.claims(), self.rejections(), notes or {}
        clauses = []
        for ident in allowlist:
            if ident in claims:
                clauses.append(f"{ident} claimed by {claims[ident]}")
            elif ident in rejections:
                clauses.append(f"{ident} rejected ({rejections[ident]})")
            else:
                clauses.append(f"{ident} {notes.get(ident, 'available')}")
        return "; ".join(clauses)

    # -- writes -------------------------------------------------------------

    def claim(self, ident: str, test: str, detail: str | None = None) -> bool:
        """Claim ``ident`` for ``test``. False when it is already claimed or rejected."""
        if (self._rejections / _file_name(ident)).exists():
            return False
        if not self._create_once(self._claims, ident, {"test": test, "detail": detail}):
            return False
        self._record(f"CLAIMED {ident} by {test}" + (f" - {detail}" if detail else ""))
        return True

    def reject(self, ident: str, reason: str) -> None:
        """Set ``ident`` aside for the rest of the run. The first reason given wins."""
        if self._create_once(self._rejections, ident, {"reason": reason}):
            self._record(f"REJECTED {ident} - {reason}")

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _create_once(directory: Path, ident: str, payload: Mapping[str, object]) -> bool:
        path = directory / _file_name(ident)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"ident": ident, "pid": os.getpid(), **payload}, fh)
        return True

    @staticmethod
    def _read_all(directory: Path) -> list[dict]:
        records = []
        for path in sorted(directory.glob("*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                # a claim file still being written by another worker: it exists, so the
                # identifier is taken, but its content is not readable yet
                records.append({"ident": path.stem, "test": "?", "reason": "?"})
        return records

    def _record(self, line: str) -> None:
        logger.info("[targets] %s", line)
        if self._log is None:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data = f"{stamp} run={self.run_id} {line}\n".encode()
        try:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data)  # one write on an O_APPEND fd: lines never interleave
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("[targets] append failed for %s: %s", self._log, exc)


# -- resolution through a paged listing -------------------------------------


@dataclass(frozen=True)
class ListingPage:
    """One page of the listing: ``(identifier, reference)`` pairs and whether it is the last."""

    items: Sequence[tuple[str, str]]
    last: bool = False


PageLookup = Callable[[int], "ListingPage | None"]


def max_pages_from_env(environ: Mapping[str, str] | None = None) -> int:
    raw = (environ if environ is not None else os.environ).get(PAGE_LIMIT_KEY, "").strip()
    if not raw:
        return DEFAULT_MAX_PAGES
    value = int(raw)
    if value < 1:
        raise ValueError(f"{PAGE_LIMIT_KEY}={raw} must be at least 1")
    return value


def resolve_targets(
    lookup_page: PageLookup, wanted: Iterable[str], *, max_pages: int = DEFAULT_MAX_PAGES
) -> dict[str, str]:
    """identifier -> reference for every wanted identifier found in the listing.

    Pages are read from 0 until every identifier is found, a page is empty or
    marked last, the lookup returns ``None`` (unreadable), a page repeats the
    previous one (a listing that ignores the page parameter), or ``max_pages``
    pages were read. Identifiers not found are simply absent from the result.
    """
    remaining = list(dict.fromkeys(wanted))
    found: dict[str, str] = {}
    previous_first: tuple[str, str] | None = None
    pages = 0
    for page in range(max_pages):
        if not remaining:
            break
        result = lookup_page(page)
        if result is None:
            logger.warning("[targets] listing page %d unreadable; stopping resolution", page)
            break
        if not result.items:
            break
        first = tuple(result.items[0])
        if first == previous_first:
            logger.warning("[targets] listing page %d repeats page %d; stopping", page, page - 1)
            break
        previous_first = first
        pages += 1
        for ident, ref in result.items:
            if ident in remaining:
                remaining.remove(ident)
                found[ident] = ref
        if result.last:
            break
    logger.info(
        "[targets] resolved %d identifier(s) in %d page(s)%s",
        len(found),
        pages,
        f"; not found: {remaining}" if remaining else "",
    )
    return found


# -- one claim for one test --------------------------------------------------


@dataclass(frozen=True)
class PreflightResult:
    """Whether a candidate suits the test. ``unsafe`` sets it aside for the whole run."""

    passed: bool
    reasons: str = ""
    unsafe: bool = False


@dataclass(frozen=True)
class Claim:
    ident: str
    reference: str


@dataclass(frozen=True)
class Outcome:
    """``claim`` is ``None`` when nothing could be handed out; ``explanation`` says why."""

    claim: Claim | None
    explanation: str


def claim_for(
    allocator: TargetPicker,
    allowlist: Sequence[str],
    test: str,
    *,
    lookup_page: PageLookup,
    preflight: Callable[[str, str], PreflightResult],
    max_pages: int | None = None,
) -> Outcome:
    """The first allowlisted identifier that is unclaimed, resolves and passes ``preflight``.

    ``preflight(ident, reference)`` decides suitability. A failure marked
    ``unsafe`` is recorded as a rejection for the whole run; any other failure
    only means "not for this test" and is noted in the explanation.
    """
    idents = list(allowlist)
    open_idents = [i for i in idents if allocator.is_available(i)]
    notes: dict[str, str] = {}
    if not open_idents:
        return Outcome(None, allocator.status(idents, notes))
    resolved = resolve_targets(
        lookup_page, open_idents, max_pages=max_pages or max_pages_from_env()
    )
    for ident in open_idents:
        reference = resolved.get(ident)
        if reference is None:
            notes[ident] = "not found in the listing"
            continue
        result = preflight(ident, reference)
        if not result.passed:
            if result.unsafe:
                allocator.reject(ident, result.reasons)
            else:
                notes[ident] = f"unsuitable for {test}: {result.reasons}"
            continue
        if allocator.claim(ident, test, f"ref {reference}"):
            return Outcome(Claim(ident, reference), f"claimed {ident}")
        notes[ident] = "claimed concurrently by another worker"
    return Outcome(None, allocator.status(idents, notes))
