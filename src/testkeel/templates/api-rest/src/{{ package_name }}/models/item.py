"""An example resource. Rename it and its schema to match the API under test."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NewItem(BaseModel):
    """What a client sends to create an item."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    price: float = Field(ge=0)
    tags: list[str] = Field(default_factory=list)


class Item(NewItem):
    """What the API returns: the request plus the server-assigned id."""

    id: int
