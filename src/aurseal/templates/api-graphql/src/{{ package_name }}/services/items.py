"""Items, through the operations in ``operations/``."""

from __future__ import annotations

from .. import operations
from ..client import GraphQLClient
from ..models import Item


class ItemsService:
    def __init__(self, client: GraphQLClient) -> None:
        self.client = client

    def by_id(self, item_id: str) -> Item | None:
        """The item, or ``None`` when the API answers ``item: null`` for an unknown id."""
        data = self.client.execute(operations.document("item_by_id"), {"id": item_id})
        return None if data["item"] is None else Item.model_validate(data["item"])
