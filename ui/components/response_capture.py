"""
Capturing the JSON the backend returned for a named call while the UI drives it.

A UI figure is only half an assertion: the screen can be right about a wrong
number or wrong about a right one. ``ResponseCapture`` keeps the latest successful
JSON body of the backend call a step triggers (say ``/quote``), so a test can
compare what the screen shows with what the server computed — and, when a figure
does not match, ``find_amount_path`` says *which* field of the response carries the
value the screen shows, which turns "the total is wrong" into "the screen shows
``totals/net`` where it should show ``totals/gross``".

Use it as a context manager so the listener is removed when the step is done::

    with ResponseCapture(page, "/quote") as quotes:
        submit_step(page, "Calculate")
    assert quotes.amount_at("totals/gross") == parse_amount(read_field(page, label="Total").raw)

Paths are ``/``-separated keys and list indexes (``items/0/price``). Amounts come
back as ``Decimal`` rounded half-up to two places.

**Waiting for a recompute after an input.** Some inputs trigger a backend recompute with
no click (a seat count typed into a field). Network idle says nothing in a single-page
app, so take a mark, make the input, and wait for a response *after* the mark::

    with ResponseCapture(page, "/price") as prices:
        mark = prices.mark()
        enter_value(page, "Seats", "7")
        assert prices.await_after(mark, timeout_ms=10000), prices.since(mark)

Every matching response is logged and kept in ``responses`` as ``(status, url,
body_head)``, refusals included, so a 422 on the recompute is visible instead of a
silent timeout; ``latest`` stays the latest 2xx JSON body.

**Rule: the baseline input must differ from the value already on the form.** Typing the
seeded value again may trigger no request at all, or one whose answer equals the stale
figure, and a fresh result becomes indistinguishable from the old one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from core.logger import logger

_CENTS = Decimal("0.01")
BODY_HEAD = 300


@dataclass(frozen=True)
class Seen:
    """One matching response: its status, URL and the start of its body."""

    status: int
    url: str
    body_head: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def value_at(document: object, path: str) -> object | None:
    node = document
    for part in (p for p in path.split("/") if p):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def to_amount(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def amount_at(document: object, path: str) -> Decimal | None:
    value = value_at(document, path)
    return to_amount(value) if isinstance(value, (int, float, str)) else None


def iter_numbers(document: object, prefix: str = "") -> Iterator[tuple[str, Decimal]]:
    """Every numeric leaf as ``(path, amount)``, in document order."""
    if isinstance(document, bool):
        return
    if isinstance(document, (int, float)):
        amount = to_amount(document)
        if amount is not None:
            yield prefix, amount
    elif isinstance(document, dict):
        for key, value in document.items():
            yield from iter_numbers(value, f"{prefix}/{key}" if prefix else str(key))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from iter_numbers(value, f"{prefix}/{index}" if prefix else str(index))


def find_amount_path(
    document: object, target: Decimal, tolerance: Decimal = Decimal("0.01")
) -> str | None:
    """The first path whose number is within ``tolerance`` of ``target``."""
    for path, amount in iter_numbers(document):
        if abs(amount - target) <= tolerance:
            return path
    return None


def first_amount(document: object, paths: Iterable[str]) -> tuple[str, Decimal] | None:
    """The first of ``paths`` that holds an amount, with the path that answered."""
    for path in paths:
        amount = amount_at(document, path)
        if amount is not None:
            return path, amount
    return None


class ResponseCapture:
    def __init__(self, page, url_match: str | re.Pattern[str] | Callable[[str], bool]) -> None:
        self.page = page
        if callable(url_match) and not isinstance(url_match, re.Pattern):
            self._matches = url_match
        elif isinstance(url_match, re.Pattern):
            self._matches = lambda url: bool(url_match.search(url))
        else:
            self._matches = lambda url: url_match in url
        self.latest: object | None = None
        self.latest_url: str | None = None
        self.count = 0
        self.responses: list[Seen] = []

    def on_response(self, response) -> None:
        if not self._matches(response.url):
            return
        try:
            body = response.body() or b""
        except Exception as exc:  # noqa: BLE001 - a redirect or aborted body is unreadable
            logger.debug("[capture] unreadable body for %s: %s", response.url, exc)
            body = b""
        head = body[:BODY_HEAD].decode("utf-8", errors="replace")
        seen = Seen(response.status, response.url, head)
        self.responses.append(seen)
        logger.info("[capture] %s %s %s", seen.status, seen.url, head[:120])
        if not seen.ok or not body:
            return
        try:
            self.latest = json.loads(body)
            self.latest_url = response.url
            self.count += 1
        except Exception as exc:  # noqa: BLE001 - a non-JSON body is just not captured
            logger.debug("[capture] skipping response %s: %s", response.url, exc)

    def mark(self) -> int:
        """A counter mark: responses after it are the ones the next input caused."""
        return len(self.responses)

    def since(self, mark: int) -> list[Seen]:
        """Every matching response after ``mark``, refusals included."""
        return list(self.responses[mark:])

    def await_after(self, mark: int, timeout_ms: int = 10000, poll_ms: int = 100) -> bool:
        """True once a response arrived after ``mark``; False when ``timeout_ms`` passes.

        Polls with ``page.wait_for_timeout`` (not ``time.sleep``) so Playwright keeps
        dispatching response events while it waits.
        """
        waited = 0
        while len(self.responses) <= mark:
            if waited >= timeout_ms:
                logger.warning("[capture] no response after mark %d within %dms", mark, timeout_ms)
                return False
            self.page.wait_for_timeout(poll_ms)
            waited += poll_ms
        return True

    def __enter__(self) -> ResponseCapture:
        self.page.on("response", self.on_response)
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            self.page.remove_listener("response", self.on_response)
        except Exception:  # noqa: BLE001
            pass

    def amount_at(self, path: str) -> Decimal | None:
        return None if self.latest is None else amount_at(self.latest, path)

    def first_amount(self, paths: Iterable[str]) -> tuple[str, Decimal] | None:
        if self.latest is None:
            return None
        found = first_amount(self.latest, paths)
        if found is None:
            preview = json.dumps(self.latest)[:1500]
            logger.warning(
                "[capture] none of the paths matched in %s; body: %s", self.latest_url, preview
            )
        return found

    def find_amount_path(self, target: Decimal, tolerance: Decimal = Decimal("0.01")) -> str | None:
        return None if self.latest is None else find_amount_path(self.latest, target, tolerance)
