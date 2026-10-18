"""Where a data-dependent test gets the entity it acts on: ``fresh``, ``reuse`` or ``targets``.

Resolved once, in one place, so no test decides this for itself.

* ``fresh`` (default) — each test creates the entity it needs. Existing records
  are never enumerated, so the run can only write to what it made itself. This
  is the CI mode.
* ``reuse`` — a pool finder picks existing entities by shape. With an
  allowlist set, only allowlisted entities are considered.
* ``targets`` — tests take their entity from the allowlist only, one per
  test. Building is refused, so a test needing a shape only its own seed gives
  is skipped instead of quietly creating data on the target environment.

Settings that contradict each other fail the run instead of one being ignored:
``targets`` without an allowlist, an allowlist under ``fresh``, or the legacy
reuse flag next to a mode other than ``reuse``.

Configuration (environment variables by default, any lookup in tests):
``SEED_MODE``, ``ENTITY_ALLOWLIST`` (inline list), ``ENTITY_ALLOWLIST_FILE``,
``SEED_REUSE_ENABLED`` (legacy).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

MODE_KEY = "SEED_MODE"
REUSE_KEY = "SEED_REUSE_ENABLED"
LITERAL_LIST_KEY = "ENTITY_ALLOWLIST"
FILE_KEY = "ENTITY_ALLOWLIST_FILE"

Lookup = Callable[[str], "str | None"]

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SEPARATORS = re.compile(r"[,;\s]+")


class SeedConfigError(ValueError):
    """The seed configuration is contradictory or unreadable; the run must not start."""


class SeedMode(str, Enum):
    FRESH = "fresh"
    REUSE = "reuse"
    TARGETS = "targets"


@dataclass(frozen=True)
class PinnedTargetList:
    """The entity identifiers a run may act on, in the order given, duplicates dropped."""

    identifiers: tuple[str, ...] = ()
    source: str = "none"

    def __bool__(self) -> bool:
        return bool(self.identifiers)

    def __contains__(self, ident: object) -> bool:
        return isinstance(ident, str) and ident.strip() in self.identifiers

    @classmethod
    def parse(
        cls, inline: str | None, file_content: str | None = None, file_name: str = ""
    ) -> PinnedTargetList:
        """Merge an inline list and a file's content (inline first).

        Separators are commas, semicolons or whitespace; ``#`` starts a comment in
        the file. A token that cannot be an identifier fails: a typo would
        otherwise never match and the run would read as "nothing to do". A file
        that adds nothing fails too, so an empty allowlist never looks filled.
        """
        seen: dict[str, None] = {}
        sources = []
        if inline and inline.strip():
            _add_tokens(inline, LITERAL_LIST_KEY, seen)
            sources.append(LITERAL_LIST_KEY)
        if file_content is not None:
            before = len(seen)
            for line in file_content.splitlines():
                _add_tokens(line.split("#", 1)[0], f"{FILE_KEY}={file_name}", seen)
            if len(seen) == before:
                raise SeedConfigError(
                    f"{FILE_KEY}={file_name} names no identifier not already listed inline"
                )
            sources.append(f"{FILE_KEY}={file_name}")
        return cls(tuple(seen), " + ".join(sources) or "none")

    @classmethod
    def resolve(cls, lookup: Lookup) -> PinnedTargetList:
        inline = lookup(LITERAL_LIST_KEY)
        file_name = (lookup(FILE_KEY) or "").strip()
        content = None
        if file_name:
            path = Path(file_name)
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SeedConfigError(
                    f"{FILE_KEY}={file_name} cannot be read ({path.resolve()}): {exc}"
                ) from exc
        return cls.parse(inline, content, file_name)


def _add_tokens(text: str, origin: str, into: dict[str, None]) -> None:
    for token in _SEPARATORS.split(text.strip()):
        if not token:
            continue
        if not _TOKEN.fullmatch(token):
            raise SeedConfigError(f"'{token}' in {origin} is not a valid identifier")
        into[token] = None


@dataclass(frozen=True)
class SeedConfig:
    mode: SeedMode
    allowlist: PinnedTargetList

    @property
    def may_build(self) -> bool:
        """Whether a test may create new data. ``targets`` refuses."""
        return self.mode is not SeedMode.TARGETS


def _blank_to_none(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def resolve(lookup: Lookup | Mapping[str, str] | None = None) -> SeedConfig:
    """Resolve the seed mode and allowlist; raises :class:`SeedConfigError` on a contradiction."""
    if lookup is None:
        lookup = os.environ.get
    elif isinstance(lookup, Mapping):
        lookup = lookup.get
    raw = _blank_to_none(lookup(MODE_KEY))
    reuse_flag = (_blank_to_none(lookup(REUSE_KEY)) or "").lower() == "true"
    allowlist_given = bool(
        _blank_to_none(lookup(LITERAL_LIST_KEY)) or _blank_to_none(lookup(FILE_KEY))
    )

    if raw is None:
        mode = SeedMode.REUSE if reuse_flag else SeedMode.FRESH
    else:
        try:
            mode = SeedMode(raw.lower())
        except ValueError:
            choices = [m.value for m in SeedMode]
            raise SeedConfigError(
                f"{MODE_KEY}={raw} is not a valid seed mode; choose from {choices}"
            ) from None
        if reuse_flag and mode is not SeedMode.REUSE:
            raise SeedConfigError(
                f"{MODE_KEY}={raw} contradicts {REUSE_KEY}=true. "
                f"Drop {REUSE_KEY} or use {MODE_KEY}=reuse."
            )
    if mode is SeedMode.FRESH and allowlist_given:
        raise SeedConfigError(
            f"an allowlist ({LITERAL_LIST_KEY} / {FILE_KEY}) is set but {MODE_KEY} resolves to "
            f"fresh, where no existing entity is ever used. Pass {MODE_KEY}=targets or reuse."
        )
    allowlist = PinnedTargetList.resolve(lookup)
    if mode is SeedMode.TARGETS and not allowlist:
        raise SeedConfigError(f"{MODE_KEY}=targets needs {LITERAL_LIST_KEY} or {FILE_KEY}")
    return SeedConfig(mode, allowlist)
