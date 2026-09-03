"""One endpoint normalisation, shared by the write path and the read path.

There used to be two, and they disagreed.

``summarizer._normalize_endpoint`` substituted ``{id}`` for UUID and numeric
path segments, so a record was **stored** as ``.../api/users/{id}/orders``.
``store._endpoint_stem`` substituted the empty string, so the same URL was
**queried** as ``LIKE '%.../api/users/orders%'``. Measured against the real
store: 0 hits. Every resource-by-id endpoint — the ones that fail most — was
written under one key and read under another, so memory of them was
unreachable.

The stem also collapsed some URLs to nothing at all. ``/123`` is entirely a
numeric segment, so it normalised to ``""``, and an empty stem meant the caller
added no SQL condition and got the whole table back. That is handled at the
call site (``MemoryStore.query``), not here — here it is enough that both sides
compute the same string.

``normalize_endpoint`` is idempotent: normalising ``/api/users/{id}`` again
leaves it unchanged, which is what lets the store normalise on insert as well
as on query without caring what the caller already did.

``escape_like`` exists because ``_`` is a single-character wildcard in SQL LIKE
and paths are full of them. Unescaped, a query for ``/api/user_profile``
matched a stored ``/api/userxprofile`` — measured. A LIKE filter that only ever
widens is not a filter.
"""

from __future__ import annotations

import re

_UUID_RE = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
_NUM_SEG_RE = re.compile(r"/\d+")

#: The placeholder both paths write for an id-shaped path segment.
ID_PLACEHOLDER = "{id}"

_PLACEHOLDER_ONLY_RE = re.compile(r"^(?:/|\{id\})*$")

_LIKE_SPECIALS_RE = re.compile(r"([\\%_])")

#: The character passed to SQL ``ESCAPE`` alongside every LIKE pattern built here.
LIKE_ESCAPE_CHAR = "\\"


def normalize_endpoint(url: str) -> str:
    """Canonical stored/queried form of a URL: lowercased, query-stripped, ids masked."""
    url = url.split("?")[0].lower().rstrip("/")
    url = _UUID_RE.sub(ID_PLACEHOLDER, url)
    return _NUM_SEG_RE.sub(f"/{ID_PLACEHOLDER}", url)


def is_discriminating(stem: str) -> bool:
    """True when a normalised endpoint narrows anything at all.

    ``/123`` is entirely a numeric segment, so it normalises to ``/{id}`` and
    its stem is just the placeholder. The old code produced ``""`` for it and
    dropped the filter outright, returning the whole table; masking the id
    instead turns that into ``LIKE '%{id}%'``, which matches every
    resource-by-id endpoint ever stored. Narrower, still not a filter.

    A stem made of nothing but placeholders and separators is therefore
    unusable, and the caller must decide what to do about it rather than
    quietly running a query that matches everything.
    """
    return not _PLACEHOLDER_ONLY_RE.fullmatch(stem)


def escape_like(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so a LIKE pattern matches them literally."""
    return _LIKE_SPECIALS_RE.sub(r"\\\1", value)
