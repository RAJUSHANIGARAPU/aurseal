"""The HTTP layer: one client, configured once, used by every service."""

from .http import ApiClient

__all__ = ["ApiClient"]
