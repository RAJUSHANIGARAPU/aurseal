"""Integrity oracles over the text of a generated document — the artifact the customer receives.

Document pipelines tend not to fail loudly. A renderer configured to tolerate
missing members prints an empty string where a field was absent, so the label
stays on the page and its value is simply gone. A broken merge delimiter is not
recognised as an expression, so the raw template text lands in the output
unchanged. Neither shows up as an HTTP error or in entity state; these checks
read the rendered text.

Every finding has a ``kind`` from :data:`KINDS`. The set is published because
a defect counts as fixed when its finding is *missing*, and a missing finding is
only meaningful from an oracle able to produce it.
:func:`all_findings` runs every oracle, so a verdict is never reached by an
observation that silently skipped one.

All functions take text already extracted (see :mod:`oracles.pdf_text`), so
they work for any output format.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from oracles.amounts import NumberFormat, number_format

KINDS = frozenset(
    {
        "UNFILLED-SLOT",
        "NULL-TEXT",
        "DANGLING-LABEL",
        "DANGLING-HEADING",
        "ABSENT-VALUE",
        "RAW-AMOUNT",
    }
)

#: Template syntax that must never survive into a document: mustache/handlebars,
#: angle-bracket ``<<[...]>>`` merge fields (and their single-bracket fault),
#: ``${...}`` and Word fields.
DEFAULT_TEMPLATE_SYNTAX: tuple[str, ...] = (
    "{{",
    "}}",
    "{%",
    "%}",
    "${",
    "<<",
    ">>",
    "<[",
    "]>",
    "</if",
    "</foreach",
    "MERGEFIELD",
    "«",
    "»",
)

_NULL_TEXT = re.compile(r"(?<![^\W\d_])(null|None|undefined|NaN|\[object Object\])(?![^\W\d_])")
_NO_CONTENT = re.compile("[\\s\u00a0€$£:.,\\-–—]")


@dataclass(frozen=True)
class Finding:
    kind: str
    detail: str
    context: str = ""

    def __str__(self) -> str:
        return f"  [{self.kind}] {self.detail}" + (
            f"\n      …{self.context}…" if self.context else ""
        )


def _excerpt(text: str, at: int) -> str:
    return re.sub(r"\s+", " ", text[max(0, at - 45) : at + 45])


def _has_content(s: str | None) -> bool:
    return bool(s) and _NO_CONTENT.sub("", s) != ""


def unfilled_slots(
    text: str | None, syntax: Iterable[str] = DEFAULT_TEMPLATE_SYNTAX
) -> list[Finding]:
    out: list[Finding] = []
    if not text:
        return out
    for token in syntax:
        start = 0
        while (at := text.find(token, start)) >= 0:
            out.append(
                Finding(
                    "UNFILLED-SLOT",
                    f"the template marker '{token}' was printed instead of a value",
                    _excerpt(text, at),
                )
            )
            start = at + len(token)
    return out


def null_texts(text: str | None) -> list[Finding]:
    """``null`` / ``None`` / ``undefined`` / ``NaN`` / ``[object Object]`` as whole words."""
    if not text:
        return []
    return [
        Finding(
            "NULL-TEXT",
            f"the word '{m.group(1)}' stands where a value belongs",
            _excerpt(text, m.start()),
        )
        for m in _NULL_TEXT.finditer(text)
    ]


def dangling_labels(text: str | None, labels: Iterable[str]) -> list[Finding]:
    """Labels with nothing after them on their line (beyond currency symbols and separators)."""
    out: list[Finding] = []
    if not text:
        return out
    for label in labels:
        for line in text.splitlines():
            if label in line and not _has_content(line[line.index(label) + len(label) :]):
                out.append(
                    Finding(
                        "DANGLING-LABEL",
                        f"'{label}' is printed with nothing after it",
                        line.strip(),
                    )
                )
    return out


def dangling_headings(text: str | None, labels: Iterable[str]) -> list[Finding]:
    """Labels heading a block: a value may sit on the label's line or within the next 3 lines.

    Kept separate from :func:`dangling_labels` on purpose: relaxing every label
    to "or the next line" would stop catching a blank row label that happens to
    be followed by unrelated content. Which rule applies is a property of the
    template, so the caller names the block labels.
    """
    out: list[Finding] = []
    if not text:
        return out
    lines = text.splitlines()
    for label in labels:
        for i, line in enumerate(lines):
            if label not in line:
                continue
            if not _has_content(line[line.index(label) + len(label) :]):
                following = next((n for n in lines[i + 1 : i + 4] if n.strip()), "")
                if not _has_content(following):
                    out.append(
                        Finding(
                            "DANGLING-HEADING",
                            f"the heading '{label}' has no content under it",
                            line.strip(),
                        )
                    )
            break
    return out


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def absent_values(
    text: str | None, expected: Mapping[str, str | None] | Iterable[str]
) -> list[Finding]:
    """Pinned text that appears nowhere in the document (whitespace-insensitive).

    ``expected`` is either ``{name: text}`` or a plain list of texts, each its own name.
    """
    if text is None:
        return []
    if not isinstance(expected, Mapping):
        expected = {value: value for value in expected}
    haystack = _normalise(text)
    return [
        Finding(
            "ABSENT-VALUE",
            f"pinned text '{value}' does not appear anywhere"
            if name == value
            else f"pinned text {name} = '{value}' does not appear anywhere",
        )
        for name, value in expected.items()
        if value and value.strip() and _normalise(value) not in haystack
    ]


def raw_amounts(
    text: str | None,
    money_fields: Iterable[str],
    locale: str | NumberFormat = "nl_NL",
    *,
    currency_marker: str | None = None,
) -> list[Finding]:
    """Amounts on money lines that were not put through the money format.

    The value may be right and the document still wrong: ``1234.56`` on an ``nl_NL``
    document means the format step was skipped. With ``currency_marker`` (``"EUR"``, say)
    an amount the marker does not precede is reported too, which catches a bare ``117.0``
    in a locale whose decimal separator it happens to share.
    """
    fmt = number_format(locale)
    if not text:
        return []
    wrong_decimal = "." if fmt.decimal == "," else ","
    pattern = re.compile(rf"\d+{re.escape(wrong_decimal)}\d{{2}}(?![\d.,])")
    unmarked = re.compile(r"-?\d[\d.,]*") if currency_marker else None
    out = []
    for label in money_fields:
        for line in text.splitlines():
            if label not in line:
                continue
            value = line[line.index(label) + len(label) :].strip()
            if unmarked is not None and (bare := unmarked.match(value)):
                out.append(
                    Finding(
                        "RAW-AMOUNT",
                        f"the amount on '{label}' reads '{bare.group()}' without "
                        f"'{currency_marker}' in front of it",
                        line.strip(),
                    )
                )
            elif m := pattern.search(line):
                out.append(
                    Finding(
                        "RAW-AMOUNT",
                        f"the amount on '{label}' reads '{m.group()}', which is not how "
                        f"{locale} writes money",
                        line.strip(),
                    )
                )
    return out


def all_findings(
    text: str | None,
    *,
    pinned_text: Mapping[str, str | None] | Iterable[str] | None = None,
    must_show: Iterable[str] = (),
    section_headings: Iterable[str] = (),
    money_fields: Iterable[str] = (),
    locale: str | NumberFormat = "nl_NL",
    syntax: Iterable[str] = DEFAULT_TEMPLATE_SYNTAX,
    currency_marker: str | None = None,
) -> list[Finding]:
    """Every oracle over one document — the observation a fixed/not-fixed verdict must use."""
    return [
        *unfilled_slots(text, syntax),
        *null_texts(text),
        *absent_values(text, pinned_text or {}),
        *dangling_labels(text, must_show),
        *dangling_headings(text, section_headings),
        *raw_amounts(text, money_fields, locale, currency_marker=currency_marker),
    ]


def describe(heading: str, findings: list[Finding]) -> str:
    """A readable multi-line failure message with a per-kind summary."""
    lines = [f"{heading} — {len(findings)} finding(s):", *map(str, findings)]
    lines.append(f"  summary: {dict(Counter(f.kind for f in findings))}")
    return "\n".join(lines)
