"""Keyed pseudonymisation of records: keep the shape of copied data, drop its values.

``mask_record(record, rules, key)`` replaces the fields named in ``rules`` with a
keyed HMAC-SHA256 derivative of the original value. The same value under the same
key always masks to the same output, so joins between masked records survive; a
different key gives unrelated output. Where it is cheap the format is preserved:

==========  ==============================================================
rule        output
==========  ==============================================================
``hash``    16 hex characters
``email``   ``u<10 hex>@example.test``
``digits``  every digit replaced by a derived digit; other characters kept
``letters`` every letter replaced by a derived letter of the same case;
            digits, spaces and punctuation kept (length preserved)
``drop``    the field is removed
==========  ==============================================================

Fields not named in ``rules`` are untouched. The key comes from the caller or
``SYNTHETIC_MASK_KEY``; it is never generated silently, because a random key
would make masking unrepeatable without anyone noticing.

This is pseudonymisation for test fixtures, not anonymisation: joined with
outside data, masked records can still be re-identifiable.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import string
from collections.abc import Mapping
from typing import Any

KEY_ENV = "SYNTHETIC_MASK_KEY"
RULES = ("hash", "email", "digits", "letters", "drop")


def mask_key(env: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if env is None else env
    key = env.get(KEY_ENV)
    if not key:
        raise ValueError(f"{KEY_ENV} is not set; masking needs an explicit key")
    return key.encode("utf-8")


def _stream(key: bytes, field: str, value: str):
    """An endless deterministic byte stream for (key, field, value)."""
    counter = 0
    while True:
        message = f"{field}\x00{value}\x00{counter}".encode()
        yield from hmac.new(key, message, hashlib.sha256).digest()
        counter += 1


def mask_value(value: Any, rule: str, key: bytes, field: str = "") -> Any:
    if rule not in RULES:
        raise ValueError(f"unknown mask rule {rule!r}; use one of {RULES}")
    if value is None:
        return None
    text = str(value)
    digest = hmac.new(key, f"{field}\x00{text}".encode(), hashlib.sha256).hexdigest()
    if rule == "hash":
        return digest[:16]
    if rule == "email":
        return f"u{digest[:10]}@example.test"
    stream = _stream(key, field, text)
    out = []
    for ch in text:
        if rule == "digits" and ch in string.digits:
            out.append(str(next(stream) % 10))
        elif rule == "letters" and ch in string.ascii_lowercase:
            out.append(string.ascii_lowercase[next(stream) % 26])
        elif rule == "letters" and ch in string.ascii_uppercase:
            out.append(string.ascii_uppercase[next(stream) % 26])
        elif rule == "letters" and ch.isalpha():
            out.append(string.ascii_lowercase[next(stream) % 26])
        else:
            out.append(ch)
    masked = "".join(out)
    return int(masked) if isinstance(value, int) and rule == "digits" else masked


def mask_record(record: Mapping[str, Any], rules: Mapping[str, str], key: bytes) -> dict:
    """A copy of ``record`` with the fields in ``rules`` masked."""
    if not key:
        raise ValueError("masking needs a non-empty key")
    unknown = sorted(set(rules.values()) - set(RULES))
    if unknown:
        raise ValueError(f"unknown mask rule(s) {unknown}; use {RULES}")
    out: dict[str, Any] = {}
    for name, value in record.items():
        rule = rules.get(name)
        if rule is None:
            out[name] = value
        elif rule != "drop":
            out[name] = mask_value(value, rule, key, name)
    return out
