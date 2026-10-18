"""Sign-in cases: one row per equivalence class, each with the outcome it must produce."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoginCase:
    id: str
    username: str
    password: str
    signed_in: bool
    error_contains: str = ""


LOGIN_CASES: tuple[LoginCase, ...] = (
    LoginCase("valid-user", "standard_user", "correct-password", signed_in=True),
    LoginCase(
        "locked-user", "locked_user", "correct-password", signed_in=False, error_contains="locked"
    ),
    LoginCase(
        "wrong-password", "standard_user", "wrong", signed_in=False, error_contains="do not match"
    ),
    LoginCase("empty-username", "", "correct-password", False, error_contains="required"),
)
