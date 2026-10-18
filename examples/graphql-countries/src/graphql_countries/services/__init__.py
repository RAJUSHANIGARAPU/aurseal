"""Services: one class per resource. They run operations and speak models, not GraphQL."""

from .countries import CountriesService

__all__ = ["CountriesService"]
