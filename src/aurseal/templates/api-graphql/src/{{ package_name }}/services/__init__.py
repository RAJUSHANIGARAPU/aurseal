"""Services: one class per resource. They run operations and speak models, not GraphQL."""

from .items import ItemsService

__all__ = ["ItemsService"]
