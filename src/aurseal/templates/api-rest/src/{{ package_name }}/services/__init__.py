"""Services: one class per resource. They speak models, not HTTP."""

from .items import ItemsService

__all__ = ["ItemsService"]
