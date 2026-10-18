"""Open a draft (or any exclusive edit session) and make sure the test cannot leave it open.

A service that allows one open draft per entity answers a second open with
409 when an earlier run abandoned one. :func:`open_draft` recovers from that by
discarding the stale draft and retrying once, then records the new draft in the
cleanup ledger so teardown discards it even when the test fails half-way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from api.clients.http_engine import ApiError, ApiResponse
from test_data.cleanup import CleanupLedger

logger = logging.getLogger(__name__)

CONFLICT = 409


def open_draft(
    open_call: Callable[[], ApiResponse],
    *,
    draft_id: Callable[[ApiResponse], str],
    discard: Callable[[str], Any],
    discard_stale: Callable[[], Any] | None = None,
    ledger: CleanupLedger | None = None,
    kind: str = "draft",
) -> str:
    """Open a draft and return its id; raises :class:`ApiError` when it cannot be opened."""
    response = open_call()
    if response.status == CONFLICT and discard_stale is not None:
        logger.warning("draft open refused with 409; discarding the stale draft and retrying")
        discard_stale()
        response = open_call()
    if not response.is_success:
        raise ApiError(response)
    ident = draft_id(response)
    if ledger is not None:
        ledger.record(kind, ident, lambda: discard(ident))
    return ident
