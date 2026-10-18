"""The ``/items`` resource."""

from __future__ import annotations

from typing import Any

from .. import contract
from ..client import ApiClient
from ..models import Item, NewItem


class ItemsService:
    path = "/items"

    def __init__(self, client: ApiClient) -> None:
        self.client = client

    def create(self, item: NewItem) -> Item:
        response = self.client.post(self.path, json=item.model_dump())
        response.raise_for_status()
        return self._parse(response.json())

    def get(self, item_id: int) -> Item:
        response = self.client.get(f"{self.path}/{item_id}")
        response.raise_for_status()
        return self._parse(response.json())

    def delete(self, item_id: int) -> None:
        self.client.delete(f"{self.path}/{item_id}").raise_for_status()

    @staticmethod
    def _parse(payload: Any) -> Item:
        contract.check("item", payload)
        return Item.model_validate(payload)
