"""The tracker adapter interface: format cases, format results, (optionally) push.

Formatters are pure: store rows in, bytes out, no network. ``push`` exists on the
interface so a live pusher can be added behind it later, but this build ships formatters
only: ``push`` with ``dry_run=True`` reports what would be sent and never touches the
transport; a real send raises :class:`PushNotSupported`. A result whose case has no
tracker id (no ``case_links`` row of kind ``tms:<tracker>``) is *reported* as unmapped,
never silently dropped. The payload shapes follow the trackers' public import formats;
they are format compatible, not integration tested against a live instance.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class PushNotSupported(RuntimeError):
    """A live push was requested; this build formats payloads only."""


@dataclass
class MappedResults:
    payload: dict[str, Any]
    unmapped: list[str] = field(default_factory=list)  # node ids without a tracker id
    omitted: list[str] = field(default_factory=list)  # results the tracker cannot express


@dataclass(frozen=True)
class PushReport:
    tracker: str
    dry_run: bool
    sent: bool
    items: int
    unmapped: tuple[str, ...] = ()
    detail: str = ""


Transport = Callable[[str, str, bytes], Any]


class TmsAdapter(Protocol):
    name: str
    link_kind: str

    def format_cases(self, cases: Sequence[Any]) -> bytes: ...

    def format_results(
        self, run: Any, results: Sequence[dict[str, Any]], mapping: dict[str, str]
    ) -> bytes: ...

    def push(self, payload: bytes, *, dry_run: bool = True) -> PushReport: ...


class FormatterOnly:
    """Shared ``push``: dry runs describe the payload; real sends are not built."""

    name = "tracker"
    link_kind = "tms:tracker"

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport  # injected for a future live pusher; never called here
        self.last: MappedResults | None = None

    def push(self, payload: bytes, *, dry_run: bool = True) -> PushReport:
        if not dry_run:
            raise PushNotSupported(
                f"live push to {self.name} is not part of this build (formatters only); "
                "import the formatted payload with the tracker's own importer"
            )
        unmapped = tuple(self.last.unmapped) if self.last else ()
        items = len(payload.splitlines()) if payload else 0
        return PushReport(self.name, True, False, items, unmapped, "dry run: nothing sent")


def mapping_from_store(store: Any, link_kind: str) -> dict[str, str]:
    """``case_id -> tracker id`` from ``case_links`` rows of ``link_kind``."""
    rows = store.conn.execute(
        "SELECT case_id, ref FROM case_links WHERE kind = ? ORDER BY case_id", (link_kind,)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


__all__ = [
    "FormatterOnly",
    "MappedResults",
    "PushNotSupported",
    "PushReport",
    "TmsAdapter",
    "Transport",
    "mapping_from_store",
]
