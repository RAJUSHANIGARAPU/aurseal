"""Services: one class per resource. They speak models, not HTTP."""

from .auth import AuthenticationFailed, AuthService
from .bookings import BookingsService

__all__ = ["AuthService", "AuthenticationFailed", "BookingsService"]
