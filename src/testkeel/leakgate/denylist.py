"""The private denylist: where it comes from, how it is parsed, and how it matches.

The list never lives in a repository. Sources, first found wins:

1. an explicit path (``--denylist``);
2. the environment variable ``LEAK_DENYLIST`` holding the list itself (a CI secret);
3. ``~/.config/testkeel/denylist.txt``;
4. ``~/.config/leak-gate/blocklist.txt`` (the older format: one regex per line), read as a
   deprecated fallback.

One entry per line; ``#`` starts a comment line:

* ``term:<phrase>`` 1-3 tokens. Matches any window of up to three tokens that spells the
  same letters, so ``acme corp`` hits ``AcmeCorp``, ``acme_corp`` and ``acme-corp.example``
  but not ``acmecorpus``. A one-token term that is a common word is a load error: use a
  ``qual:`` entry or a distinctive codename instead.
* ``qual:<qualified.name>`` a token sequence of two or more tokens (``acme_server.billing``)
  that matches exactly that sequence; for module names that are common words alone.
* ``re:<regex>`` a regular expression searched in each normalised line.

A line without a prefix is a ``term:`` (in the older file: a ``re:``).

Nothing here ever returns or prints an entry's text: errors and findings name the entry by
its line number only.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from testkeel.leakgate.normalise import Token, normalise, tokens

ENV_VAR = "LEAK_DENYLIST"
CONFIG_PATH = Path(".config") / "testkeel" / "denylist.txt"
LEGACY_PATH = Path(".config") / "leak-gate" / "blocklist.txt"
MAX_TERM_TOKENS = 3
_SYSTEM_WORDS = Path("/usr/share/dict/words")
# Always treated as common, also where no system word list exists.
COMMON_WORDS = frozenset(
    """
    about account accounts admin agent alert api app application archive audit auth
    backend backup bank base batch billing board book build bundle cache calendar card
    cart case catalog catalogue channel chart check checkout client cloud code config
    connector console contract core cost customer customers dashboard data database
    debug deploy design dev device docs document domain email engine entity error event
    events export feature file files finance flow form gateway graph health home hub
    identity import index invoice invoices item job jobs key ledger library link list log
    login mail main manager map media message model module monitor network node note
    notification order orders page panel partner payment payments people platform plugin
    policy portal price pricing product profile project projects proxy query queue
    record records release report reports request resource router rule run runner
    sales schedule scheduler schema search security server service services session
    settings shop site source storage store stream subscription support sync system
    task team template test tests ticket token tool tracker user users utils vault
    view web worker workflow
    """.split()
)


class DenylistError(ValueError):
    """The list cannot be used. The message never contains an entry's text."""


@dataclass(frozen=True)
class Entry:
    kind: str  # term | qual | re
    line: int
    words: tuple[str, ...] = ()
    pattern: re.Pattern[str] | None = None

    @property
    def rule(self) -> str:
        return f"deny.{self.kind}"

    @property
    def entry_id(self) -> str:
        return f"e{self.line}"


@dataclass(frozen=True)
class Denylist:
    entries: tuple[Entry, ...]
    source: str  # a description of where it came from, never its content

    def __bool__(self) -> bool:
        return bool(self.entries)


@dataclass
class _Index:
    joined: dict[str, list[Entry]] = field(default_factory=dict)
    sequences: dict[tuple[str, ...], list[Entry]] = field(default_factory=dict)
    longest: int = MAX_TERM_TOKENS


def common_words(extra: Path | None = _SYSTEM_WORDS) -> frozenset[str]:
    """Embedded common words plus the system word list when present."""
    words = set(COMMON_WORDS)
    if extra is not None:
        try:
            words.update(w.strip().casefold() for w in extra.read_text("utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            pass
    return frozenset(w for w in words if w)


def parse(
    text: str,
    source: str = "denylist",
    legacy: bool = False,
    wordlist: Iterable[str] | None = None,
) -> Denylist:
    """Parse list text; :class:`DenylistError` names the bad entry by line number only."""
    words = frozenset(wordlist) if wordlist is not None else common_words()
    entries: list[Entry] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kind, sep, value = line.partition(":")
        if not sep or kind not in {"term", "qual", "re"}:
            kind, value = ("re" if legacy else "term"), line
        value = value.strip()
        if not value:
            raise DenylistError(f"{source}: entry at line {number} is empty")
        if kind == "re":
            try:
                pattern = re.compile(value, re.IGNORECASE)
            except re.error:
                raise DenylistError(
                    f"{source}: entry at line {number} is not a valid regex"
                ) from None
            entries.append(Entry("re", number, pattern=pattern))
            continue
        parts = tuple(tokens(value))
        if not parts:
            raise DenylistError(f"{source}: entry at line {number} has no letters or digits")
        if kind == "term":
            if len(parts) > MAX_TERM_TOKENS:
                raise DenylistError(
                    f"{source}: term at line {number} has more than {MAX_TERM_TOKENS} "
                    "tokens; use qual: for a qualified name"
                )
            if len(parts) == 1 and parts[0] in words:
                raise DenylistError(
                    f"{source}: term at line {number} is a common word; use qual: "
                    "(e.g. name_server.module) or a distinctive name"
                )
        elif len(parts) < 2:
            raise DenylistError(f"{source}: qual at line {number} needs two or more tokens")
        entries.append(Entry(kind, number, words=parts))
    return Denylist(tuple(entries), source)


def load(
    path: Path | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    wordlist: Iterable[str] | None = None,
) -> Denylist | None:
    """The first available list (see module docstring), or None when there is none.

    An empty list is returned as an empty :class:`Denylist` (falsy), so the caller can
    treat "missing" and "empty" alike.
    """
    env = os.environ if environ is None else environ
    if path is not None:
        return parse(_read(path), "--denylist file", wordlist=wordlist)
    if env.get(ENV_VAR, "").strip():
        return parse(env[ENV_VAR], f"${ENV_VAR}", wordlist=wordlist)
    base = home if home is not None else Path.home()
    if (base / CONFIG_PATH).is_file():
        return parse(_read(base / CONFIG_PATH), "~/" + CONFIG_PATH.as_posix(), wordlist=wordlist)
    if (base / LEGACY_PATH).is_file():
        return parse(
            _read(base / LEGACY_PATH),
            "~/" + LEGACY_PATH.as_posix() + " (deprecated location)",
            legacy=True,
            wordlist=wordlist,
        )
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DenylistError(f"cannot read the denylist file: {exc.__class__.__name__}") from None


class Matcher:
    """Matches a denylist against token streams and lines."""

    def __init__(self, denylist: Denylist) -> None:
        self._index = _Index()
        self._regexes = [e for e in denylist.entries if e.kind == "re"]
        for entry in denylist.entries:
            if entry.kind == "term":
                self._index.joined.setdefault("".join(entry.words), []).append(entry)
            elif entry.kind == "qual":
                self._index.sequences.setdefault(entry.words, []).append(entry)
                self._index.longest = max(self._index.longest, len(entry.words))

    def tokens(self, stream: Sequence[Token]) -> list[tuple[int, Entry]]:
        """(line, entry) hits; a window may span one line break and reports its first line."""
        hits: list[tuple[int, Entry]] = []
        index = self._index
        for start in range(len(stream)):
            first = stream[start].line
            for size in range(1, index.longest + 1):
                end = start + size
                if end > len(stream) or stream[end - 1].line - first > 1:
                    break
                window = tuple(t.text for t in stream[start:end])
                if size <= MAX_TERM_TOKENS:
                    for entry in index.joined.get("".join(window), ()):
                        hits.append((first, entry))
                for entry in index.sequences.get(window, ()):
                    hits.append((first, entry))
        return hits

    def lines(self, text: str) -> list[tuple[int, Entry]]:
        """(line, entry) hits of the ``re:`` entries, per normalised line."""
        if not self._regexes:
            return []
        hits: list[tuple[int, Entry]] = []
        for number, line in enumerate(text.splitlines(), start=1):
            folded = normalise(line)
            hits.extend(
                (number, e) for e in self._regexes if e.pattern and e.pattern.search(folded)
            )
        return hits
