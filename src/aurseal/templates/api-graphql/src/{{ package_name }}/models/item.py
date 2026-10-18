"""An example type. Rename it, and its operation, to match the API under test."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Item(BaseModel):
    """The fields ``ItemFields`` selects: a model per selection set, not per schema type."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    name: str = Field(min_length=1)
    price: float = Field(ge=0)
    tags: list[str]
