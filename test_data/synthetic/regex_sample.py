"""Produce a string that matches a (simple) regular expression, deterministically.

Enough of the regex language for field patterns: literals and escapes, ``.``,
``\\d \\w \\s``, character classes with ranges (not negated), groups with
alternation, and the quantifiers ``? * + {n} {n,} {n,m}`` (open repeats are
capped). Anchors are accepted and ignored. Anything else raises
:class:`UnsupportedPattern` rather than producing a value that may not match.
Every result is verified with ``re.fullmatch`` before it is returned.
"""

from __future__ import annotations

import random
import re
import string

_OPEN_REPEAT_CAP = 6
_CLASSES = {
    "d": string.digits,
    "w": string.ascii_letters + string.digits + "_",
    "s": " ",
}
_DOT = string.ascii_letters + string.digits


class UnsupportedPattern(ValueError):
    pass


class _Parser:
    def __init__(self, pattern: str, rng: random.Random) -> None:
        self.p = pattern
        self.i = 0
        self.rng = rng

    def peek(self) -> str | None:
        return self.p[self.i] if self.i < len(self.p) else None

    def take(self) -> str:
        ch = self.p[self.i]
        self.i += 1
        return ch

    # alternation := sequence ('|' sequence)*
    def alternation(self) -> str:
        branches = [self.sequence()]
        while self.peek() == "|":
            self.take()
            branches.append(self.sequence())
        return self.rng.choice(branches)

    # Every branch is generated (to keep the parse aligned), then one is chosen.
    def sequence(self) -> str:
        out: list[str] = []
        while self.peek() not in (None, "|", ")"):
            atom = self.atom()
            if atom is None:
                continue
            low, high = self.quantifier()
            count = self.rng.randint(low, high)
            out.append("".join(atom() for _ in range(count)))
        return "".join(out)

    def atom(self):
        ch = self.take()
        if ch in "^$":
            return None
        if ch == "(":
            if self.p.startswith("?:", self.i):
                self.i += 2
            elif self.peek() == "?":
                raise UnsupportedPattern(f"group syntax at {self.i} in {self.p!r}")
            start = self.i
            self.alternation()  # advance past the group once to find its end
            if self.peek() != ")":
                raise UnsupportedPattern(f"unclosed group in {self.p!r}")
            end = self.i
            self.take()
            body = self.p[start:end]
            rng = self.rng
            return lambda: _Parser(body, rng).full()
        if ch == "[":
            chars = self.char_class()
            return lambda: self.rng.choice(chars)
        if ch == ".":
            return lambda: self.rng.choice(_DOT)
        if ch == "\\":
            esc = self.take()
            if esc in _CLASSES:
                pool = _CLASSES[esc]
                return lambda: self.rng.choice(pool)
            if esc.isalnum():
                raise UnsupportedPattern(f"escape \\{esc} in {self.p!r}")
            return lambda: esc
        if ch in "*+?{)|":
            raise UnsupportedPattern(f"unexpected {ch!r} at {self.i - 1} in {self.p!r}")
        return lambda: ch

    def char_class(self) -> str:
        if self.peek() == "^":
            raise UnsupportedPattern(f"negated character class in {self.p!r}")
        chars: list[str] = []
        while True:
            if self.peek() is None:
                raise UnsupportedPattern(f"unclosed character class in {self.p!r}")
            ch = self.take()
            if ch == "]" and chars:
                break
            if ch == "\\":
                esc = self.take()
                chars.extend(_CLASSES.get(esc, esc))
                continue
            if self.peek() == "-" and self.i + 1 < len(self.p) and self.p[self.i + 1] != "]":
                self.take()
                end = self.take()
                chars.extend(chr(c) for c in range(ord(ch), ord(end) + 1))
            else:
                chars.append(ch)
        return "".join(chars)

    def quantifier(self) -> tuple[int, int]:
        ch = self.peek()
        if ch == "?":
            self.take()
            return 0, 1
        if ch == "*":
            self.take()
            return 0, _OPEN_REPEAT_CAP
        if ch == "+":
            self.take()
            return 1, _OPEN_REPEAT_CAP
        if ch == "{":
            match = re.match(r"\{(\d+)(,(\d*))?\}", self.p[self.i :])
            if not match:
                raise UnsupportedPattern(f"bad repeat in {self.p!r}")
            self.i += match.end()
            low = int(match.group(1))
            if match.group(2) is None:
                return low, low
            high = int(match.group(3)) if match.group(3) else low + _OPEN_REPEAT_CAP
            return low, high
        return 1, 1

    def full(self) -> str:
        value = self.alternation()
        if self.peek() is not None:
            raise UnsupportedPattern(f"unbalanced ')' in {self.p!r}")
        return value


def sample(pattern: str, rng: random.Random) -> str:
    """A string ``s`` with ``re.fullmatch(pattern, s)``; raises when unsupported."""
    value = _Parser(pattern, rng).full()
    if not re.fullmatch(pattern, value):
        raise UnsupportedPattern(f"sampler produced {value!r}, which {pattern!r} rejects")
    return value
