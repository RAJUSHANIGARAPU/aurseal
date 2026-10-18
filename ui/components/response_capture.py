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
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from core.logger import logger

_CENTS = Decimal("0.01")


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

    def on_response(self, response) -> None:
        if not self._matches(response.url) or not 200 <= response.status < 300:
            return
        try:
            body = response.body()
            if not body:
                return
            self.latest = json.loads(body)
            self.latest_url = response.url
            self.count += 1
        except Exception as exc:  # noqa: BLE001 - a non-JSON body is just not captured
            logger.debug("[capture] skipping response %s: %s", response.url, exc)

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
