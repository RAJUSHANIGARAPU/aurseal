"""Shared helpers for the lifecycle examples: the frozen run day, fixtures, small API steps.

Every helper goes through the project's own layers (``HttpEngine``, ``FixtureSeeder``,
the oracles); nothing here talks to the demo's internals.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from api.clients.http_engine import ApiResponse, HttpEngine
from demo_sut.customers import make_iban
from oracles.charges import ChargeSchedule
from test_data.fixture_replay import Fixture
from test_data.pool import ReusePool

logger = logging.getLogger(__name__)

#: The instant every lifecycle example freezes the demo clock at, and its date.
NOW = "2026-03-10T09:00:00Z"
TODAY = date(2026, 3, 10)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: The demo's billing model as oracle data: no surcharge by payment term, 20 % tax.
DEMO_CHARGES = ChargeSchedule({1: Decimal(0), 3: Decimal(0), 12: Decimal(0)}, Decimal("0.20"))

_accounts = itertools.count(1)


def new_account_number() -> str:
    """A checksum-valid, invented account number, unique within the session."""
    return make_iban(bban=f"LIFE{next(_accounts):010d}")


def load_fixture(name: str) -> Fixture:
    return Fixture.load(FIXTURES / f"{name}.json")


def ok(response: ApiResponse) -> Any:
    """The JSON body of a 2xx answer; anything else fails with the response quoted."""
    return response.raise_for_status().json()


def subscription(api: HttpEngine, sub_id: Any) -> dict[str, Any]:
    return ok(api.get(f"subscriptions/{sub_id}"))


def book_due(api: HttpEngine, sub_id: Any, until: str | None = None) -> list[int]:
    """Book this subscription's due periods (never anyone else's)."""
    body: dict[str, Any] = {"subscription_id": int(sub_id)}
    if until:
        body["until"] = until
    return ok(api.post("jobs/run", json=body))["booked"]


def transactions(api: HttpEngine, sub_id: Any) -> list[dict[str, Any]]:
    return ok(api.get(f"subscriptions/{sub_id}/transactions"))


def as_rows(txs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Transactions as ledger rows: dates parsed, ``end`` inclusive (the demo's is exclusive)."""
    rows = []
    for tx in sorted(txs, key=lambda t: (t["period_start"], t["id"])):
        start = date.fromisoformat(tx["period_start"])
        end = date.fromisoformat(tx["period_end"]) - timedelta(days=1)
        rows.append({**tx, "start": start, "end": end})
    return rows


def open_renewal(api: HttpEngine, sub_id: Any, **fields: Any) -> dict[str, Any]:
    return ok(api.post(f"subscriptions/{sub_id}/drafts", json={"kind": "renew", **fields}))


def confirm_draft(api: HttpEngine, draft_id: Any) -> dict[str, Any]:
    return ok(api.post(f"drafts/{draft_id}/actions/confirm"))


def run_visible_steps(
    draft: Mapping[str, Any], handlers: Mapping[str, Callable[[], Any]]
) -> list[str]:
    """Run the handler of every step the draft currently shows, in its order; return what ran.

    Steps are regenerated per draft, so a step that is not listed is not driven - it is
    neither required nor assumed.
    """
    ran = []
    for step in draft.get("steps") or []:
        handler = handlers.get(step)
        if handler is not None:
            handler()
            ran.append(step)
        else:
            logger.info("step %s has nothing to fill; submitted as prefilled", step)
    return ran


#: Refusals that provably did not act: the service says so (429 = throttled, nothing stored).
SAFE_TO_RESEND = (429,)


def retry_create(
    create: Callable[[], ApiResponse],
    *,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[ApiResponse, int]:
    """Send a create, and send it again only after a refusal that stored nothing.

    A 429 is resent after its ``Retry-After`` seconds, at most ``attempts`` sends in all.
    Anything else that is not a success - a 409 or 422 (a decision), a 504 or no answer
    (the call may have acted) - ends the loop with the response quoted.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last: ApiResponse | None = None
    attempt = 0
    for attempt in range(1, attempts + 1):
        last = create()
        if last.is_success:
            return last, attempt
        if last.status not in SAFE_TO_RESEND:
            break
        wait = float(last.header("Retry-After") or 0)
        logger.warning("create throttled on attempt %d; resending after %ss", attempt, wait)
        sleep(wait)
    raise AssertionError(f"create not accepted (attempt {attempt} of {attempts}): {last}")


def renewable_pool(api: HttpEngine) -> ReusePool:
    """Active subscriptions whose term ends within the window and whose periods are booked."""

    def fit(sub: Mapping[str, Any]) -> bool:
        if sub["status"] != "active" or sub.get("open_draft_id"):
            return False
        if date.fromisoformat(sub["term_end"]) > TODAY + timedelta(days=90):
            return False
        before_end = [
            t for t in transactions(api, sub["id"]) if t["period_start"] < sub["term_end"]
        ]
        return bool(before_end) and all(t["state"] == "BOOKED" for t in before_end)

    return ReusePool(
        lambda: ok(api.get("subscriptions", params={"status": "active"})),
        fit=fit,
        label=lambda s: s["number"],
        preference=lambda s: -s["id"],
    )
