"""A generic UN/EDIFACT interchange parser with envelope completeness checks.

An interchange is a string of segments (``TAG+element+comp:comp'``). The
service characters default to ``:`` component, ``+`` element, ``.`` decimal,
``?`` release (escape), space reserved and ``'`` segment terminator; a ``UNA``
service string advice at the start overrides them.

:func:`parse_interchange` honours the release character (``?+`` is a literal
plus, ``??`` a literal question mark), splits every element into its
components, ignores line breaks between segments, and groups segments into
messages on ``UNH`` .. ``UNT``.

:meth:`Interchange.completeness` checks the envelope counts a receiver checks:
each ``UNT`` counts its message's segments (``UNH`` and ``UNT`` included) and
repeats the ``UNH`` reference; ``UNZ`` counts the messages and repeats the
``UNB`` control reference.

:func:`group_by` splits a message into groups that each start at a given tag,
for formats that model entities as a start segment plus its followers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field


class EdifactError(ValueError):
    """The interchange cannot be parsed."""


@dataclass(frozen=True)
class ServiceChars:
    component: str = ":"
    element: str = "+"
    decimal: str = "."
    release: str = "?"
    reserved: str = " "
    terminator: str = "'"


@dataclass(frozen=True)
class Segment:
    tag: str
    elements: tuple[tuple[str, ...], ...]
    """Element -> components. ``elements[0]`` is the first data element after the tag."""

    def element(self, index: int, default: str = "") -> str:
        """First component of data element ``index`` (0-based), or ``default``."""
        return self.component(index, 0, default)

    def component(self, index: int, comp: int, default: str = "") -> str:
        try:
            return self.elements[index][comp]
        except IndexError:
            return default

    def __str__(self) -> str:
        return self.tag + "".join("+" + ":".join(e) for e in self.elements)


@dataclass
class Message:
    reference: str
    message_type: str
    segments: list[Segment] = field(default_factory=list)
    """``UNH`` .. ``UNT`` inclusive."""

    def find(self, tag: str) -> list[Segment]:
        return [s for s in self.segments if s.tag == tag]

    def first(self, tag: str) -> Segment | None:
        return next((s for s in self.segments if s.tag == tag), None)


@dataclass
class Interchange:
    service: ServiceChars
    segments: list[Segment]
    messages: list[Message]

    def find_message(self, predicate: Callable[[Message], bool]) -> Message | None:
        return next((m for m in self.messages if predicate(m)), None)

    def completeness(self) -> list[str]:
        """Envelope count and reference mismatches; empty when the interchange is complete."""
        problems = []
        for m in self.messages:
            unt = m.segments[-1] if m.segments and m.segments[-1].tag == "UNT" else None
            if unt is None:
                problems.append(f"message {m.reference}: no UNT")
                continue
            declared = unt.element(0)
            if declared != str(len(m.segments)):
                problems.append(
                    f"message {m.reference}: UNT declares {declared} segments, "
                    f"found {len(m.segments)}"
                )
            if unt.element(1) != m.reference:
                problems.append(
                    f"message {m.reference}: UNT reference {unt.element(1)!r} differs from UNH"
                )
        unb = next((s for s in self.segments if s.tag == "UNB"), None)
        unz = next((s for s in self.segments if s.tag == "UNZ"), None)
        if unb is not None and unz is None:
            problems.append("UNB without UNZ")
        if unz is not None:
            if unz.element(0) != str(len(self.messages)):
                problems.append(
                    f"UNZ declares {unz.element(0)} messages, found {len(self.messages)}"
                )
            if unb is not None and unz.element(1) != unb.element(4):
                problems.append(
                    f"UNZ reference {unz.element(1)!r} differs from UNB {unb.element(4)!r}"
                )
        return problems


def _split(text: str, sep: str, release: str) -> list[str]:
    """Split on ``sep`` outside release escapes; escapes are kept for the next level."""
    parts, buf, i = [], [], 0
    while i < len(text):
        ch = text[i]
        if ch == release and i + 1 < len(text):
            buf.append(text[i : i + 2])
            i += 2
            continue
        if ch == sep:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _unescape(text: str, release: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == release and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def parse_interchange(text: str) -> Interchange:
    body = text.lstrip("﻿")
    service = ServiceChars()
    if body.startswith("UNA"):
        if len(body) < 9:
            raise EdifactError("UNA service string advice is shorter than 6 characters")
        c = body[3:9]
        service = ServiceChars(c[0], c[1], c[2], c[3], c[4], c[5])
        body = body[9:]
    segments: list[Segment] = []
    for raw in _split(body, service.terminator, service.release):
        raw = raw.strip("\r\n")
        if not raw.strip():
            continue
        parts = _split(raw, service.element, service.release)
        tag = parts[0].strip()
        if not tag.isalnum():
            raise EdifactError(f"segment {raw[:30]!r} has no valid tag")
        elements = tuple(
            tuple(
                _unescape(c, service.release) for c in _split(p, service.component, service.release)
            )
            for p in parts[1:]
        )
        segments.append(Segment(tag, elements))
    return Interchange(service, segments, _messages(segments))


def _messages(segments: Sequence[Segment]) -> list[Message]:
    messages: list[Message] = []
    current: Message | None = None
    for seg in segments:
        if seg.tag == "UNH":
            if current is not None:
                messages.append(current)  # UNH without UNT: keep it, completeness reports it
            current = Message(seg.element(0), seg.component(1, 0), [seg])
        elif current is not None:
            current.segments.append(seg)
            if seg.tag == "UNT":
                messages.append(current)
                current = None
    if current is not None:
        messages.append(current)
    return messages


def group_by(segments: Iterable[Segment], start_tag: str) -> list[list[Segment]]:
    """Groups that each begin at ``start_tag`` and run to the next one; leading segments drop."""
    groups: list[list[Segment]] = []
    for seg in segments:
        if seg.tag == start_tag:
            groups.append([seg])
        elif groups and seg.tag not in ("UNT", "UNZ"):
            groups[-1].append(seg)
    return groups
