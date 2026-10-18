"""Role-based access control: four fixed roles, a permission matrix that is data.

Authorisation happens in :class:`~qaplatform.service.PlatformService` and nowhere else:
every service method names the permission it needs and the service's single scope check
calls :func:`authorize`, so the API, the UI and the CLI cannot disagree about who may do
what. The matrix is not user-editable; widening it is a visible diff that a test pins.

Identity is API tokens only. A token carries a role; its owner holds a role binding per
project; the effective role is the lower of the two, recomputed on every request.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from qaplatform.errors import Forbidden


class Role(str, Enum):
    VIEWER = "viewer"  # read everything in the project
    TESTER = "tester"  # + push results, record manual and exploratory work (CI tokens)
    LEAD = "lead"  # + trigger runs, read the audit log
    ADMIN = "admin"  # + members, tokens, project settings

    @property
    def rank(self) -> int:
        return ORDER.index(self)


ORDER = (Role.VIEWER, Role.TESTER, Role.LEAD, Role.ADMIN)


class Permission(str, Enum):
    READ = "read"
    PUSH_RESULTS = "push_results"
    RECORD_MANUAL = "record_manual"
    TRIGGER_RUN = "trigger_run"
    READ_AUDIT = "read_audit"
    MANAGE_TOKENS = "manage_tokens"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_PROJECT = "manage_project"


_VIEWER = frozenset({Permission.READ})
_TESTER = _VIEWER | {Permission.PUSH_RESULTS, Permission.RECORD_MANUAL}
_LEAD = _TESTER | {Permission.TRIGGER_RUN, Permission.READ_AUDIT}
_ADMIN = _LEAD | {
    Permission.MANAGE_TOKENS,
    Permission.MANAGE_MEMBERS,
    Permission.MANAGE_PROJECT,
}

MATRIX: Mapping[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.TESTER: frozenset(_TESTER),
    Role.LEAD: frozenset(_LEAD),
    Role.ADMIN: frozenset(_ADMIN),
}


def lower(a: str, b: str) -> str:
    """The weaker of two roles."""
    return min(Role(a), Role(b), key=lambda r: r.rank).value


def allows(role: str, permission: str) -> bool:
    return Permission(permission) in MATRIX[Role(role)]


def authorize(role: str, permission: str) -> None:
    """Raise :class:`Forbidden` unless ``role`` holds ``permission``."""
    if not allows(role, permission):
        raise Forbidden(f"role {role!r} lacks the {Permission(permission).value!r} permission")


def lowest_role_with(permission: str) -> Role:
    return next(r for r in ORDER if Permission(permission) in MATRIX[r])


__all__ = [
    "MATRIX",
    "ORDER",
    "Permission",
    "Role",
    "allows",
    "authorize",
    "lower",
    "lowest_role_with",
]
