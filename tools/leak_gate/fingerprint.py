"""Language-agnostic word tokenizer, k-gram hashing and winnowing.

Normalisation (applied identically to the private source and to this repo):

* only WORDS are tokens: identifiers and numbers (``[A-Za-z_$][A-Za-z0-9_$]*``
  and ``\\d[\\w.]*``). Punctuation, operators, quote characters and comment
  markers (``//``, ``#``, ``/** *``, ``<!--``) are dropped, so a Java method
  and its Python port, or a Javadoc and the docstring copied from it,
  tokenize alike. Words inside strings and comments are kept: copied prose
  and copied embedded scripts are leaks too;
* declaration and type keywords that a port rewrites (``def``/``function``,
  ``self``/``this``, ``public``, ``static``, ``int``/``long``/``str``,
  ``None``/``null``, ...) are dropped: they carry no copy signal and would
  otherwise break every k-gram of a cross-language port;
* identifiers are case- and separator-folded: ``contractNumber``,
  ``contract_number`` and ``CONTRACT_NUMBER`` are one token. They are not
  blanked to a placeholder: that turns shared boilerplate into matches;
* whitespace and line breaks never matter.

Line mapping: every token sits on exactly one line. A k-gram is ``K``
consecutive tokens and covers every line from its first token's line to its
last token's line. A line is a code line when at least one token is on it;
blank, punctuation-only and marker-only lines are not.

Winnowing (Schleimer, Wilkerson, Aiken 2003) keeps the minimum hash of every
window of ``W`` consecutive k-gram hashes, so a shared run of at least
``W + K - 1`` tokens keeps at least one fingerprint, and kept fingerprints are
at most ``W`` k-grams apart. With ``K >= W`` the kept k-grams inside a copied
region overlap, so the checker sees the region as contiguous, losing at most
``W - 1`` tokens at each edge.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

K = 10  # tokens per k-gram
W = 8  # winnowing window in k-grams; must stay <= K (see module docstring)

_WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d[\w.]*")
# Compared after folding (lower case, no "_" or "$").
STOPWORDS = frozenset(
    "def function fn func fun var let const val final public private protected static "
    "void new self this int long short byte char double float boolean bool str string "
    "none null nil undefined async await override".split()
)


@dataclass(frozen=True)
class Token:
    text: str
    line: int


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    for number, line in enumerate(source.splitlines(), 1):
        for m in _WORD.finditer(line):
            text = m.group().replace("_", "").replace("$", "").lower()
            if text and text not in STOPWORDS:
                tokens.append(Token(text, number))
    return tokens


def _hash(parts: Sequence[str]) -> int:
    digest = hashlib.blake2b(
        "\x1f".join(parts).encode("utf-8"), digest_size=8, person=b"leakgate-kgram1"
    ).digest()
    return int.from_bytes(digest, "big")


def kgram_hashes(tokens: Sequence[Token], k: int = K) -> list[int]:
    texts = [t.text for t in tokens]
    return [_hash(texts[i : i + k]) for i in range(len(texts) - k + 1)]


def winnow(hashes: Sequence[int], w: int = W) -> set[int]:
    """Keep the minimum hash of every window of ``w`` consecutive hashes."""
    if len(hashes) <= w:
        return {min(hashes)} if hashes else set()
    return {min(hashes[i : i + w]) for i in range(len(hashes) - w + 1)}


def fingerprint_text(source: str) -> set[int]:
    return winnow(kgram_hashes(tokenize(source)))


def code_lines(tokens: Iterable[Token]) -> set[int]:
    return {t.line for t in tokens}


@dataclass(frozen=True)
class Run:
    start: int
    end: int
    matched: int  # matched code lines inside [start, end]


def runs(matched: set[int], code: set[int], min_lines: int, max_gap: int) -> list[Run]:
    """Maximal runs of matched code lines.

    Walking the code lines in order, consecutive matched code lines join one
    run while at most ``max_gap`` unmatched code lines separate them (a small
    edit breaks a few k-grams, not the copy). Non-code lines neither count nor
    break a run. A run is reported when it holds at least ``min_lines``
    matched code lines; gap lines do not count towards that.
    """
    found: list[Run] = []
    start = end = None
    count = gap = 0
    for line in sorted(code):
        if line in matched:
            if start is None:
                start = line
            end, count, gap = line, count + 1, 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                if count >= min_lines:
                    found.append(Run(start, end, count))
                start, count, gap = None, 0, 0
    if start is not None and count >= min_lines:
        found.append(Run(start, end, count))
    return found
