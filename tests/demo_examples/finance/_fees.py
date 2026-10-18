"""Read a subscription's setup-fee block as an observed shape.

An absent fee is not a zero fee: a response that dropped ``fees`` (or one of its
fields) must not read as "this subscription charges nothing". :class:`FeeShape`
wraps :class:`test_data.shape.ObservedShape`, so an unobserved value stays ``None``
and a predicate that needs it raises :class:`~test_data.shape.ShapeNotObserved`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from test_data.shape import ObservedShape

FIELDS = ("net", "tax", "invoiced")


@dataclass(frozen=True)
class FeeShape:
    shape: ObservedShape

    @classmethod
    def of(cls, subscription: Mapping[str, Any]) -> FeeShape:
        return cls(
            ObservedShape.observe(
                f"fees of {subscription.get('number', '?')}",
                subscription,
                {name: f"fees.{name}" for name in FIELDS},
            )
        )

    def all_present(self) -> bool:
        return all(self.shape.observed(name) for name in FIELDS)

    def all_zero(self) -> bool:
        """Net and tax are both observed and zero (raises when either was not observed)."""
        self.shape.require("net", "tax")
        return Decimal(str(self.shape.get("net"))) == 0 == Decimal(str(self.shape.get("tax")))

    def charges_fee(self) -> bool:
        """The subscription charges a setup fee (raises when the net fee was not observed)."""
        self.shape.require("net")
        return Decimal(str(self.shape.get("net"))) > 0

    def describe(self) -> str:
        return self.shape.describe()
