"""Text normalisation and tokenisation shared by list entries and scanned text.

The same function is applied to both sides, so ``Acme Corp``, ``acme_corp``, ``AcmeCorp``,
``ＡＣＭＥ corp`` and ``acme-corp.example`` all reduce to the tokens ``acme corp``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Latin look-alikes from Cyrillic and Greek (paste accidents, not a full UTS #39 skeleton).
_CONFUSABLES = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
        "ј": "j", "ѕ": "s", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h",
        "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "І": "I", "К": "K",
        "М": "M", "Т": "T", "В": "B", "Н": "H",
        "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
        "υ": "u", "χ": "x", "Α": "A", "Β": "B", "Ε": "E", "Ι": "I", "Κ": "K", "Μ": "M",
        "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
    }
)  # fmt: skip
_WORD_RE = re.compile(r"[^\W_]+")


def _strip_format(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def fold(text: str) -> str:
    """NFKC, format characters removed, confusables folded; case kept."""
    return _strip_format(unicodedata.normalize("NFKC", text)).translate(_CONFUSABLES)


def normalise(text: str) -> str:
    """:func:`fold` then casefold: the form ``re:`` entries are matched against."""
    return fold(text).casefold()


def _kind(ch: str) -> str:
    if ch.isdigit():
        return "d"
    return "u" if ch.isupper() else "l"


def _pieces(word: str) -> list[str]:
    """Case boundaries ("fooBar" -> foo|Bar, "HTTPServer" -> HTTP|Server) and
    letter/digit boundaries ("v2beta" -> v|2|beta)."""
    out: list[str] = []
    start = 0
    for i in range(1, len(word)):
        prev, cur = _kind(word[i - 1]), _kind(word[i])
        nxt = _kind(word[i + 1]) if i + 1 < len(word) else ""
        if (
            (prev == "d") != (cur == "d")
            or (prev == "l" and cur == "u")
            or (prev == "u" and cur == "u" and nxt == "l")
        ):
            out.append(word[start:i])
            start = i
    out.append(word[start:])
    return out


def tokens(text: str) -> list[str]:
    """Normalised tokens: split on non-alphanumerics, case and letter/digit boundaries."""
    out: list[str] = []
    for word in _WORD_RE.findall(fold(text)):
        out.extend(piece.casefold() for piece in _pieces(word))
    return out


@dataclass(frozen=True)
class Token:
    text: str
    line: int


def line_tokens(text: str) -> list[Token]:
    """Tokens of a multi-line text, each with its 1-based line number."""
    return [
        Token(token, number)
        for number, line in enumerate(text.splitlines(), start=1)
        for token in tokens(line)
    ]
