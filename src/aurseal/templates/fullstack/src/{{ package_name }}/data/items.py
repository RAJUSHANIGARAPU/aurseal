"""Item data both layers share: the API creates it, the UI looks for it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from ..models import NewItem


@dataclass(frozen=True)
class ItemBuilder:
    """A valid item by default; each ``with_*`` returns a builder with one thing changed.

    Every built name carries a fresh suffix, so tests running in parallel against a shared
    environment never see each other's rows.
    """

    prefix: str = "item"
    price: float = 9.99
    tags: tuple[str, ...] = ()

    def with_price(self, price: float) -> ItemBuilder:
        return replace(self, price=price)

    def with_tags(self, *tags: str) -> ItemBuilder:
        return replace(self, tags=tags)

    def build(self) -> NewItem:
        """A validated item: an invalid combination fails here, not at the API."""
        name = f"{self.prefix}-{uuid.uuid4().hex[:8]}"
        return NewItem(name=name, price=self.price, tags=list(self.tags))
