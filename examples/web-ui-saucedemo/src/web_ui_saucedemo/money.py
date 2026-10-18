"""Amounts as the shop shows them ("Total: $43.18"), parsed to exact decimals."""

from __future__ import annotations

import re
from decimal import Decimal

_AMOUNT = re.compile(r"\$(\d+(?:\.\d{2})?)")


def dollars(text: str) -> Decimal:
    match = _AMOUNT.search(text)
    if match is None:
        raise ValueError(f"no dollar amount in {text!r}")
    return Decimal(match.group(1))
