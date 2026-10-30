"""
Masking live credentials and personal data in the artifacts a test run archives.

CI keeps three kinds of file after a run: Playwright trace zips, run logs and report
attachments. Each records what the browser and the framework saw, which includes the
test user's *live* session: cookie values, ``Authorization`` headers, CSRF tokens,
bearer tokens, OAuth ``code``/``state`` parameters, and pre-signed URL signatures.
Anyone who can download the artifact can replay that session until it expires.

Why a module of its own rather than an extension of ``contract_testing/redaction.py``:
that module decides by *name* (a header or query parameter called ``x-auth-token``)
for documents that describe a request's shape. Here the input is free text and trace
event streams where the name is often absent, so the decision is by *value shape* or
by *known value*. The two share only the ``<redacted>`` marker, which is imported so
one grep finds both.

Two entry points for text:

* :func:`credentials` masks tokens only (session, bearer, OAuth, signed URLs).
* :func:`redact` does that and also masks personal data: an email keeps its domain,
  an IBAN keeps its country and last four characters.

Every pattern is linear-time. There is no alternation or nested quantifier over an
unbounded run: runs start at a lookbehind so a long value is scanned once, and
repeats are bounded. A 20 KB cookie value costs the same as a short one.

Trace rewriting (:func:`redact_trace`) fails closed: if anything goes wrong the trace
is deleted rather than kept unredacted. Limits are listed in
``docs/features/artifact-redaction.md``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

from contract_testing.redaction import REDACTED
from core.logger import logger

# A value shorter than this is not treated as a secret to hunt for: masking a
# one-character "cookie" everywhere would shred the file and protects nothing.
MIN_KNOWN_LENGTH = 6

SENSITIVE_HEADERS = frozenset(
    {"cookie", "set-cookie", "authorization", "proxy-authorization", "x-xsrf-token", "x-csrf-token"}
)
_EVENT_SUFFIXES = (".trace", ".network")
_TEXT_SUFFIXES = (".json", ".html", ".txt")

# ── tokens ───────────────────────────────────────────────────────────────────

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
SECRET_PARAMS = (
    "code",
    "state",
    "session_state",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "client_secret",
    "api_key",
    "apikey",
    "signature",
    "sig",
    "x-amz-signature",
    "x-amz-security-token",
    "x-amz-credential",
)
_SENSITIVE_PAIR_NAMES = SENSITIVE_HEADERS | frozenset(SECRET_PARAMS)
_URL_SECRET = re.compile(
    r"(?P<lead>[?&;\s\"'])(?P<name>" + "|".join(SECRET_PARAMS) + r")=(?P<value>[^&\s\"'<>#\\]+)",
    re.IGNORECASE,
)
# "cookie: a=b; c=d", "Authorization: Bearer x" as a plain log line. The value stops at
# the end of the line or of the JSON string holding it.
_HEADER_LINE = re.compile(
    r"(?P<name>\b(?:set-cookie|cookie|authorization|proxy-authorization|x-xsrf-token"
    r"|x-csrf-token))(?P<sep>[ \t]*:[ \t]*)(?P<value>[^\r\n\"\\]+)",
    re.IGNORECASE,
)

# ── personal data ────────────────────────────────────────────────────────────

# The lookbehind makes a match start only at the beginning of a run of address
# characters, so a long run without "@" is scanned once instead of once per position.
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@(?P<domain>[A-Za-z0-9.-]{1,255})")
_IBAN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9])"
)


def _mask_url_secret(match: re.Match[str]) -> str:
    return f"{match['lead']}{match['name']}={REDACTED}"


def _mask_header_line(match: re.Match[str]) -> str:
    return f"{match['name']}{match['sep']}{REDACTED}"


def credentials(text: str) -> str:
    """Mask tokens and session material in ``text``; personal data is left alone."""
    if not text:
        return text
    text = _HEADER_LINE.sub(_mask_header_line, text)
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _URL_SECRET.sub(_mask_url_secret, text)


def _mask_email(match: re.Match[str]) -> str:
    domain = match["domain"]
    trailing = len(domain) - len(domain.rstrip(".-"))
    host = domain[: len(domain) - trailing] if trailing else domain
    if "." not in host:
        return match.group(0)
    return f"***@{host}{domain[len(host) :]}"


def _iban_valid(compact: str) -> bool:
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


def _mask_iban(match: re.Match[str]) -> str:
    raw = match.group(0)
    compact = raw.replace(" ", "")
    if not _iban_valid(compact):
        return raw
    return f"{compact[:2]}{'*' * (len(compact) - 6)}{compact[-4:]}"


def redact(text: str) -> str:
    """Mask tokens and personal data: email keeps its domain, IBAN its country and last 4."""
    if not text:
        return text
    text = credentials(text)
    text = _EMAIL.sub(_mask_email, text)
    return _IBAN.sub(_mask_iban, text)


# ── known values ─────────────────────────────────────────────────────────────


def _variants(value: str) -> set[str]:
    """The value as it appears raw, JSON-escaped and percent-encoded."""
    return {value, json.dumps(value, ensure_ascii=False)[1:-1], quote(value, safe="")}


def _needles(values: Iterable[str]) -> list[str]:
    found: set[str] = set()
    for value in values:
        if isinstance(value, str) and len(value) >= MIN_KNOWN_LENGTH:
            found |= _variants(value)
    return sorted((n for n in found if n), key=len, reverse=True)


def mask_known(text: str, needles: list[str]) -> str:
    for needle in needles:
        if needle in text:
            text = text.replace(needle, REDACTED)
    return text


def cookie_values(context) -> list[str]:
    """
    Current cookie values of a Playwright browser context.

    Read these *before* the trace is written: the values are what the trace holds.
    A failure returns an empty list; the shape-based masking still applies.
    """
    try:
        return [c["value"] for c in context.cookies() if c.get("value")]
    except Exception as exc:  # noqa: BLE001 - evidence never fails a test
        logger.warning("cookie values unavailable for trace redaction: %s", exc)
        return []


# ── trace events ─────────────────────────────────────────────────────────────


def _scrub(node, needles: list[str]):
    """Rewrite a parsed JSON value: header shapes and cookie lists, then every string."""
    if isinstance(node, dict):
        out = {}
        # HAR-style {"name": ..., "value": ...}: a header, or a query-string parameter.
        sensitive = str(node.get("name", "")).lower() in _SENSITIVE_PAIR_NAMES
        for key, value in node.items():
            if key == "value" and sensitive and isinstance(value, str):
                out[key] = REDACTED
            elif key == "cookies" and isinstance(value, list):
                out[key] = [_scrub_cookie(c, needles) for c in value]
            else:
                out[key] = _scrub(value, needles)
        return out
    if isinstance(node, list):
        return [_scrub(item, needles) for item in node]
    if isinstance(node, str):
        return credentials(mask_known(node, needles))
    return node


def _scrub_cookie(cookie, needles: list[str]):
    if isinstance(cookie, dict) and isinstance(cookie.get("value"), str):
        return {
            **_scrub({k: v for k, v in cookie.items() if k != "value"}, needles),
            "value": REDACTED,
        }
    return _scrub(cookie, needles)


def _rewrite_event_line(line: str, needles: list[str]) -> str:
    if not line.strip():
        return line
    try:
        rewritten = json.dumps(
            _scrub(json.loads(line), needles), separators=(",", ":"), ensure_ascii=False
        )
    except ValueError:
        rewritten = credentials(line)
    # Keys, odd encodings and unparseable lines: one last pass for the known values.
    return mask_known(rewritten, needles)


def _rewrite_events(data: bytes, needles: list[str]) -> bytes:
    text = data.decode("utf-8")
    lines = text.split("\n")
    return "\n".join(_rewrite_event_line(line, needles) for line in lines).encode("utf-8")


def _rewrite_text(data: bytes, needles: list[str]) -> bytes:
    return credentials(mask_known(data.decode("utf-8"), needles)).encode("utf-8")


def _rewrite_member(name: str, data: bytes, needles: list[str]) -> bytes:
    lowered = name.lower()
    if lowered.endswith(_EVENT_SUFFIXES):
        return _rewrite_events(data, needles)
    if lowered.endswith(_TEXT_SUFFIXES):
        try:
            return _rewrite_text(data, needles)
        except UnicodeDecodeError:
            # Not the text its name claims; a binary under a text name is left as is.
            return data
    return data


def redact_trace(path: str | Path, known_values: Iterable[str] = ()) -> bool:
    """
    Rewrite the trace zip at ``path`` in place with credentials masked.

    ``known_values`` are exact values (cookie values read from the context before the
    trace was written) masked wherever they occur. Returns True when the file is
    redacted. Fails closed: on any error the trace is deleted and False is returned,
    so an unredacted trace is never left behind.
    """
    target = Path(path)
    scratch = target.with_name(target.name + ".redacting")
    try:
        needles = _needles(known_values)
        with zipfile.ZipFile(target) as source, zipfile.ZipFile(scratch, "w") as sink:
            for info in source.infolist():
                data = source.read(info)
                if not info.is_dir():
                    data = _rewrite_member(info.filename, data, needles)
                sink.writestr(info, data, compress_type=info.compress_type)
        os.replace(scratch, target)
        return True
    except Exception as exc:  # noqa: BLE001 - fail closed on anything
        logger.warning("trace redaction failed, deleting %s: %s", target.name, type(exc).__name__)
        for leftover in (scratch, target):
            try:
                leftover.unlink(missing_ok=True)
            except OSError as unlink_exc:
                logger.error("could not delete unredacted %s: %s", leftover.name, unlink_exc)
        return False


# ── logs ─────────────────────────────────────────────────────────────────────


class RedactingFilter(logging.Filter):
    """Masks tokens and personal data in a record before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not hide the record
            message = str(record.msg)
        record.msg, record.args = redact(message), None
        return True
