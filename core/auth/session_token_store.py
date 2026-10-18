"""
Log in once, reuse the session: a file cache for the token and the browser state.

A UI login costs seconds per test and hammers the identity provider; a suite of
any size should log in once and hand every browser context the saved Playwright
``storage_state`` (cookies + localStorage). This module owns the two files that
make that possible and decides when they are still fit to use.

**JWT expiry is read from the token**, with a safety margin, because a token that
expires mid-test fails in a way that looks like a product defect. The payload is
base64url (RFC 7515: ``-`` and ``_``, padding stripped). Decoding it needs the
URL-safe alphabet *and* restored padding; translating the characters to the
standard alphabet and then feeding a URL-safe decoder rejects every payload that
contains them, and a token that cannot be read is treated as expired — so a valid
login gets thrown away on every run for roughly a third of all tokens.

**Opaque session tokens carry no expiry**, so their freshness is the age of the
storage-state file against the server's session lifetime.

**Writes are atomic** (temp file + ``os.replace``). The cache is shared by every
run and every xdist worker on the machine; two logins finishing together used to
be able to leave a file holding the tail of one write behind the head of another,
which parses as nothing and fails every context that loads it. A reader now sees
the old file or the new one. An unreadable file is handled as if it were missing:
the run signs in afresh rather than passing a corrupt file along.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from core.logger import logger

DEFAULT_MARGIN_S = 60
DEFAULT_SESSION_TTL_S = 900
CACHE_DIR_VARIABLE = "TEST_AUTH_CACHE_DIR"


def default_cache_dir() -> Path:
    override = os.environ.get(CACHE_DIR_VARIABLE, "").strip()
    return Path(override) if override else Path.home() / ".cache" / "test-auth"


def looks_like_jwt(token: str | None) -> bool:
    return bool(token) and token.count(".") == 2 and token.startswith("eyJ")


def b64url_decode(segment: str) -> bytes:
    """Decode one base64url segment, restoring the padding JWTs strip."""
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def jwt_claims(token: str | None) -> dict | None:
    """The payload of a JWT as a dict, or None when it is not a readable JWT."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        claims = json.loads(b64url_decode(parts[1]))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def jwt_expiry(token: str | None) -> float | None:
    """The ``exp`` claim in epoch seconds, or None when there is none."""
    claims = jwt_claims(token)
    if not claims or "exp" not in claims:
        return None
    try:
        return float(claims["exp"])
    except (TypeError, ValueError):
        return None


def jwt_is_usable(
    token: str | None, margin_s: float = DEFAULT_MARGIN_S, now: float | None = None
) -> bool:
    """
    True when ``token`` is a readable JWT that stays valid beyond ``margin_s``.

    A JWT without ``exp`` is usable (it does not expire); an unreadable one is not.
    """
    if not looks_like_jwt(token):
        return False
    claims = jwt_claims(token)
    if claims is None:
        return False
    expiry = jwt_expiry(token)
    if expiry is None:
        return "exp" not in claims
    return expiry > (time.time() if now is None else now) + margin_s


def _sanitize_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)[:120] or "default"


def write_atomically(target: Path, content: str) -> None:
    """Replace ``target`` in one rename; readers never see a partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=target.name, suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def is_readable_json_object(path: Path) -> bool:
    """True when ``path`` holds exactly one JSON object (trailing data rejects it)."""
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError) as exc:
        logger.warning(
            "cached storage state %s is unreadable (%s), logging in again", path.name, exc
        )
        return False


class SessionTokenStore:
    """The cached token and browser storage state for one login (``key``)."""

    def __init__(
        self,
        key: str,
        cache_dir: str | Path | None = None,
        session_ttl_s: float = DEFAULT_SESSION_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self.key = _sanitize_key(key)
        self.session_ttl_s = session_ttl_s
        self._clock = clock

    @property
    def token_path(self) -> Path:
        return self.cache_dir / f"{self.key}.token"

    @property
    def storage_state_path(self) -> Path:
        return self.cache_dir / f"{self.key}.storage.json"

    # ── token ────────────────────────────────────────────────────────────────

    def is_usable(self, token: str | None, margin_s: float = DEFAULT_MARGIN_S) -> bool:
        """JWT by its ``exp``; an opaque token by the age of the paired storage state."""
        if not token or not token.strip():
            return False
        if looks_like_jwt(token):
            return jwt_is_usable(token, margin_s, self._clock())
        return self.storage_state_age_s() < self.session_ttl_s - margin_s

    def load_token(self, margin_s: float = DEFAULT_MARGIN_S) -> str | None:
        """The cached token while usable; an expired one is deleted."""
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("token cache unreadable: %s", exc)
            return None
        if self.is_usable(token, margin_s):
            return token
        self.token_path.unlink(missing_ok=True)
        logger.debug("cached token expired, discarded")
        return None

    def save_token(self, token: str) -> None:
        try:
            write_atomically(self.token_path, token)
        except OSError as exc:
            logger.warning("token cache write failed: %s", exc)

    # ── storage state ────────────────────────────────────────────────────────

    def storage_state_age_s(self) -> float:
        try:
            return self._clock() - self.storage_state_path.stat().st_mtime
        except OSError:
            return float("inf")

    def storage_state_valid(self) -> bool:
        """Fresh enough for the server session and parses as a JSON object."""
        return self.storage_state_age_s() < self.session_ttl_s and is_readable_json_object(
            self.storage_state_path
        )

    def load_storage_state(self) -> dict | None:
        if not self.storage_state_valid():
            return None
        return json.loads(self.storage_state_path.read_text(encoding="utf-8"))

    def save_storage_state(self, state: dict | str) -> Path:
        content = state if isinstance(state, str) else json.dumps(state)
        write_atomically(self.storage_state_path, content)
        return self.storage_state_path

    def invalidate(self) -> None:
        """Forget this login, forcing a fresh one next time."""
        for path in (self.token_path, self.storage_state_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not invalidate %s: %s", path.name, exc)
