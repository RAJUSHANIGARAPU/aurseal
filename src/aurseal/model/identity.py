"""Test identity: explicit ids and the versioned fingerprint.

The same test must get the same identity across renames that keep an explicit id,
parametrisation, browser/project matrices, shards and retries. Resolution order:

1. an explicit id in a tag or the title (``@case:PAY-123``, ``[PAY-123]``, ``@PAY-123``);
2. (server side) an automation reference declared by a case file;
3. the fingerprint of the normalised ``(framework, path, suite, name)`` tuple.

Normalisation rules (``IDENTITY_RULES_VERSION``):

* N1 path: repository-relative, ``/`` separators, no leading ``./``, Unicode NFC, case kept.
* N2 parameters: a trailing ``[...]`` parameter id is a *variant*, not part of the name.
* N3 matrix: browser, Playwright project, OS and runtime versions are a configuration and
  never enter the fingerprint (the fingerprint has no parameter for them).
* N4 shards: ``shard-3`` style and worker prefixes are stripped from suite names.
* N6 whitespace: runs collapsed, trimmed; suite levels joined with `` › ``.
* N7 explicit-id removal: the id token is removed from the title before fingerprinting,
  so adding an id later does not change the fingerprint.

Changing any rule or the formula changes every fingerprint: that is a major release.
"""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence

IDENTITY_RULES_VERSION = "v1"
SUITE_SEPARATOR = " › "
_UNIT_SEPARATOR = "\x1f"

EXPLICIT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_ID = r"[A-Z][A-Z0-9]+-\d+"
# Title tokens that carry an explicit id: [PAY-123], @case:PAY-123, @PAY-123.
_TITLE_ID_RE = re.compile(rf"\[({_ID})\]|@case:({_ID})\b|(?<![\w@])@({_ID})\b")
_SHARD_RE = re.compile(r"^\[?(?:shard|worker|gw)[-_ ]?\d+\]?[ :]?", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_VARIANT_RE = re.compile(r"^(?P<name>.*\S)\[(?P<variant>[^\[\]]*)\]$", re.DOTALL)


def _clean(text: str) -> str:
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def explicit_id(title: str = "", tags: Iterable[str] = ()) -> str | None:
    """The explicit case id carried by a tag or the title, or None.

    Tags win over the title. A tag may be ``PAY-123``, ``@PAY-123`` or ``case:PAY-123``.
    """
    for tag in tags:
        value = tag.strip().lstrip("@")
        if value.lower().startswith("case:"):
            value = value[len("case:") :]
        if EXPLICIT_ID_RE.match(value):
            return value
    match = _TITLE_ID_RE.search(title)
    if match:
        return next(group for group in match.groups() if group)
    return None


def strip_explicit_id(title: str) -> str:
    """N7: the title without its explicit-id tokens, whitespace normalised."""
    return _clean(_TITLE_ID_RE.sub(" ", title))


def split_variant(name: str) -> tuple[str, str | None]:
    """N2: ``test_x[EUR-10]`` -> ``("test_x", "EUR-10")``; no trailing ``[...]`` -> no variant.

    A bracket at the start of a title (``[PAY-123] refund``) is not a parameter id.
    """
    match = _VARIANT_RE.match(name)
    if not match:
        return name, None
    return match.group("name"), match.group("variant")


def normalise_path(path: str) -> str:
    """N1."""
    text = re.sub(r"/{2,}", "/", unicodedata.normalize("NFC", path.strip()).replace("\\", "/"))
    while text.startswith("./"):
        text = text[2:]
    return text


def normalise_suite(suite: str | Sequence[str]) -> str:
    """N4 + N6: one suite path string from levels (or an already joined path)."""
    levels = suite.split(SUITE_SEPARATOR) if isinstance(suite, str) else list(suite)
    cleaned = [_clean(_SHARD_RE.sub("", _clean(level))) for level in levels]
    return SUITE_SEPARATOR.join(level for level in cleaned if level)


def normalise_name(name: str) -> str:
    """N6 + N7 (the parameter split, N2, is the importer's job: it keeps the variant)."""
    return strip_explicit_id(name)


def fingerprint(
    framework: str,
    path: str,
    suite: str | Sequence[str],
    name: str,
    project_id: str = "",
) -> str:
    """``base32(sha256("v1" | project | framework | path | suite | name)[:20])``.

    Inputs are normalised here, so two callers cannot disagree by normalising differently.
    160 bits, 32 upper-case base32 characters, no padding.
    """
    parts = (
        IDENTITY_RULES_VERSION,
        project_id,
        framework.strip().lower(),
        normalise_path(path),
        normalise_suite(suite),
        normalise_name(name),
    )
    digest = hashlib.sha256(_UNIT_SEPARATOR.join(parts).encode("utf-8")).digest()[:20]
    return base64.b32encode(digest).decode("ascii")
