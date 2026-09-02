"""
Keeping credentials out of contract files.

Contracts are written to ``contracts/`` (``config.py`` — a repo-relative path,
not gitignored) and are meant to be committed and reviewed. So anything that
reaches a contract reaches source control.

Request headers were already being scrubbed, which shows the risk was seen. But
the scrub was a three-name denylist — ``authorization``, ``cookie``,
``x-api-key`` — and the Postman path used a two-name one that did not even
include ``x-api-key``. A denylist fails open on every name nobody thought to
write down: ``x-auth-token``, ``api-key``, ``authentication``,
``proxy-authorization``, ``x-amz-security-token``.

And the URL query string, parsed three lines from the header scrub, was stored
verbatim. ``GET /reports?api_key=live_abc123`` put that key straight into the
JSON. Presigned URLs (``?X-Amz-Signature=``), one-time tokens and session ids
all travel in query strings, so this was the wider hole of the two.

Both are handled here by one rule, because the thing that went wrong was having
two rules and only maintaining one.

**Redacted, not dropped.** A contract describes the shape of a request, and
"this endpoint requires an authorization header" is part of that shape —
deleting the header loses it. The provider side validates responses only
(``provider.validate_response``), so nothing compares these values and keeping
the key costs nothing.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode

REDACTED = "<redacted>"

# Substring match, deliberately. Real header and parameter names compose these
# words freely — x-auth-token, api_key, X-Amz-Security-Token, access_token,
# refresh_token, client_secret — and an exact-match list is what failed before.
# A false positive costs a redacted value in a document nobody validates; a
# false negative commits a live credential.
_SENSITIVE = re.compile(
    r"auth|token|secret|passw|credential|session|cookie|bearer|signature|"
    r"(^|[^a-z])sig([^a-z]|$)|(^|[^a-z])key([^a-z]|$)|apikey|otp|nonce",
    re.IGNORECASE,
)


def is_sensitive_name(name: str) -> bool:
    """True if a header or query-parameter name suggests it carries a secret."""
    return bool(_SENSITIVE.search(name or ""))


def redact_headers(headers: dict) -> dict[str, str]:
    """Replace the value of every credential-carrying header, keeping the name."""
    return {
        key: (REDACTED if is_sensitive_name(key) else value) for key, value in headers.items()
    }


def redact_query(query: str) -> str:
    """
    Replace the value of every credential-carrying query parameter.

    Segments carrying no ``=`` are redacted whole. They are not key/value pairs,
    so their content is unlabelled and cannot be classified — and a bare signed
    blob (``?eyJhbGciOiJIUzI1NiJ9``) is exactly the shape a token arrives in.
    Handling this explicitly matters because ``parse_qsl(keep_blank_values=True)``
    reads such a segment as a *name* with an empty value, which would sail past a
    name-based check and out to disk.

    Blank values are otherwise preserved, so the parameter set survives the round
    trip and the contract still records which parameters the endpoint takes.
    """
    if not query:
        return ""

    # Each segment is encoded on its own so an opaque one can be replaced by the
    # bare marker. `safe` keeps the placeholder legible rather than percent-
    # encoding it to %3Credacted%3E, so one grep finds it in headers and queries
    # alike.
    parts: list[str] = []
    for segment in query.split("&"):
        if not segment:
            continue
        if "=" not in segment:
            parts.append(REDACTED)
            continue
        parts.extend(
            urlencode([(key, REDACTED if is_sensitive_name(key) else value)], safe="<>")
            for key, value in parse_qsl(segment, keep_blank_values=True)
        )
    return "&".join(parts)
