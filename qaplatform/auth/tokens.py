"""API tokens: ``qap_`` + 32 random bytes, stored as a SHA-256 hash only, shown once.

A token belongs to a user, is scoped to one project (or, for an instance admin, to the
whole instance), carries a role, and always expires. Verification looks the hash up, then
compares in constant time, then checks revocation, expiry, the owner (not disabled) and the
owner's role binding: the effective role is the lower of the token's role and the binding,
so removing a member or lowering a role takes effect on the next request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from qaplatform.errors import BadRequest, Unauthorized
from qaplatform.rbac import lower

TOKEN_PREFIX = "qap_"
DEFAULT_TTL_DAYS = 90
MAX_TTL_DAYS = 366
ROLES = ("viewer", "tester", "lead", "admin")
_TOUCH_EVERY_S = 60  # last_used_at is refreshed at most once a minute per token


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Who is calling: resolved from a token on every request."""

    user_id: str
    token_id: str | None  # None: a local operator (the CLI on the server's own database)
    role: str
    project_id: str | None  # None: an instance token, usable on every project
    expires_at: str

    @property
    def instance(self) -> bool:
        return self.project_id is None

    def to_json(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "token_id": self.token_id,
            "role": self.role,
            "project": self.project_id,
            "instance": self.instance,
            "expires_at": self.expires_at,
        }


def local_operator(user_id: str = "local-operator") -> Principal:
    """The principal of someone running the CLI against the database file directly.

    File access already means full control of the store, so this is an instance admin; it
    exists so CLI changes go through the service and land in the audit log like any other.
    """
    return Principal(user_id=user_id, token_id=None, role="admin", project_id=None, expires_at="")


def issue(
    store: Any,
    *,
    user_id: str,
    project_id: str | None,
    role: str,
    label: str = "",
    ttl_days: int = DEFAULT_TTL_DAYS,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Create a token; returns ``(raw token, stored row without the hash)``.

    The raw value exists only in the return value: print it once, never log it.
    """
    if role not in ROLES:
        raise BadRequest(f"role must be one of {', '.join(ROLES)}")
    if not 1 <= int(ttl_days) <= MAX_TTL_DAYS:
        raise BadRequest(f"ttl_days must be between 1 and {MAX_TTL_DAYS}")
    if project_id is None and role != "admin":
        raise BadRequest("an instance token is an admin token")
    user = store.user(user_id)
    if user is None or user["disabled"]:
        raise BadRequest(f"no active user {user_id!r}")
    if project_id is None:
        if not user["is_instance_admin"]:
            raise BadRequest("only an instance admin can hold an instance token")
    else:
        binding = store.binding(user_id, project_id)
        if binding is None:
            raise BadRequest(f"user {user_id!r} has no role in project {project_id!r}")
        if lower(role, binding["role"]) != role:
            raise BadRequest(
                f"a {role!r} token exceeds the owner's {binding['role']!r} role in the project"
            )
    now = now or datetime.now(timezone.utc)
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = {
        "id": "tok_" + uuid.uuid4().hex[:16],
        "project_id": project_id,
        "user_id": user_id,
        "role": role,
        "token_hash": hash_token(raw),
        "label": label,
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(days=int(ttl_days))),
    }
    store.insert_token(row)
    public = {k: v for k, v in row.items() if k != "token_hash"}
    return raw, public


def verify(store: Any, raw: str | None, *, now: datetime | None = None) -> Principal:
    """The principal behind ``raw``; :class:`Unauthorized` when missing, unknown, revoked
    or expired (the message never says which, beyond what the caller already knows)."""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        raise Unauthorized("a bearer token is required")
    digest = hash_token(raw)
    row = store.token_by_hash(digest)
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        raise Unauthorized("token not recognised")
    now = now or datetime.now(timezone.utc)
    if row.get("revoked_at"):
        raise Unauthorized("token revoked")
    if _parse(row["expires_at"]) <= now:
        raise Unauthorized("token expired")
    user = store.user(row["user_id"])
    if user is None or user["disabled"]:
        raise Unauthorized("token owner is disabled")
    role = row["role"]
    if row["project_id"] is None:
        if not user["is_instance_admin"]:
            raise Unauthorized("token owner is no longer an instance admin")
    else:
        binding = store.binding(row["user_id"], row["project_id"])
        if binding is None:
            raise Unauthorized("token owner no longer has a role in the project")
        role = lower(role, binding["role"])
    last = row.get("last_used_at")
    if not last or (now - _parse(last)).total_seconds() >= _TOUCH_EVERY_S:
        store.touch_token(row["id"], _iso(now))
    return Principal(
        user_id=row["user_id"],
        token_id=row["id"],
        role=role,
        project_id=row["project_id"],
        expires_at=row["expires_at"],
    )


def bearer(headers: Any) -> str | None:
    """The token from an ``Authorization: Bearer`` header (case-insensitive lookup)."""
    value = None
    for key in ("authorization", "Authorization"):
        if hasattr(headers, "get") and headers.get(key):
            value = headers.get(key)
            break
    if not value:
        return None
    scheme, _, token = str(value).partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


__all__ = [
    "DEFAULT_TTL_DAYS",
    "ROLES",
    "TOKEN_PREFIX",
    "Principal",
    "bearer",
    "hash_token",
    "issue",
    "local_operator",
    "verify",
]
