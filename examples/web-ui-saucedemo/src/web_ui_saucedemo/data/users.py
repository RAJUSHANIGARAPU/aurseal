"""Sign-in cases. The users and the password are the ones the Sauce Demo login page itself
lists for anyone to use; they are demo data, not credentials."""

from __future__ import annotations

from dataclasses import dataclass

PASSWORD = "secret_sauce"  # shown on the public login page  # noqa: S105


@dataclass(frozen=True)
class LoginCase:
    id: str
    username: str
    password: str
    signed_in: bool
    error_contains: str = ""


LOGIN_CASES: tuple[LoginCase, ...] = (
    LoginCase("standard-user", "standard_user", PASSWORD, signed_in=True),
    # Answers slowly on purpose: passes only because every check waits for the page.
    LoginCase("performance-glitch-user", "performance_glitch_user", PASSWORD, signed_in=True),
    LoginCase("locked-out-user", "locked_out_user", PASSWORD, False, error_contains="locked out"),
    LoginCase(
        "wrong-password", "standard_user", "wrong", False, error_contains="do not match any user"
    ),
    LoginCase("missing-username", "", PASSWORD, False, error_contains="Username is required"),
    LoginCase(
        "missing-password", "standard_user", "", False, error_contains="Password is required"
    ),
)
