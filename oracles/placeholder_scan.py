"""Read a document template's source and check its placeholders before rendering.

Rendering a template with one sample payload only tells you about the fields that sample
fills. Two faults slip through that way:

* a placeholder naming a field the payload never carries. Lenient engines substitute an
  empty string, so the document looks tidy and is simply missing a value;
* a placeholder with a damaged bracket. The engine stops recognising it and copies the
  raw text into the document.

Both are visible in the source, so this module works on source only: it lists the paths a
template reads, reports damaged placeholders, describes conditional blocks and value
filters, and checks each path against the JSON schema of the render payload. Checks on
rendered output live in :mod:`oracles.document_findings`.

Two dialects are understood, chosen with ``syntax``:

``"mustache"`` (default)
    ``{{ invoice.total }}``, ``{{ invoice.total|money }}``,
    ``{{#if payer.iban}}...{{else}}...{{/if}}``, ``{{#each lines}}...{{/each}}``.
``"mergefield"``
    Word field codes such as ``MERGEFIELD payer_name \\* MERGEFORMAT``.

Word ``.docx`` packages are read with :func:`package_source_text`, which strips the XML
markup from the raw parts instead of reading text runs: a word processor often saves one
typed placeholder across several runs, and only the stripped XML shows the string the
template engine will actually see.

Standard library only.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Conditional",
    "ValueFilter",
    "conditional_blocks",
    "damaged_placeholders",
    "is_compound_file",
    "is_zip_package",
    "package_source_text",
    "placeholder_paths",
    "schema_declares",
    "top_level_names",
    "undeclared_paths",
    "value_filters",
]

# Typographic quote marks a word processor substitutes for straight ones while typing.
TYPOGRAPHIC_QUOTES = frozenset("“”‘’")
_ANY_QUOTE = "\"'" + "".join(sorted(TYPOGRAPHIC_QUOTES))

_ZIP_MAGIC = b"PK\x03\x04"
_COMPOUND_FILE_MAGIC = bytes((0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1))

# Words that look like a path but never name payload data.
_NOT_DATA = frozenset({"this", "true", "false"})

_IDENT = r"[A-Za-z_]\w*"
_DOTTED = rf"{_IDENT}(?:\.{_IDENT})*"


@dataclass(frozen=True)
class Conditional:
    """One conditional block: what it tests and whether it has a fallback branch."""

    guard: str
    has_alternative: bool


@dataclass(frozen=True)
class ValueFilter:
    """The filter attached to one placeholder, with the quotes around its argument.

    ``text`` is the quoted argument when there is one, else the whole filter text.
    Both quotes are ``""`` when the filter carries no quoted argument.
    """

    text: str
    left_quote: str
    right_quote: str

    @property
    def uses_typographic_quote(self) -> bool:
        """True when either quote is a curly one, which most engines will not parse."""
        return self.left_quote in TYPOGRAPHIC_QUOTES or self.right_quote in TYPOGRAPHIC_QUOTES

    @property
    def quotes_disagree(self) -> bool:
        """True when one side is a straight double quote and the other a curly one."""
        pair = (self.left_quote, self.right_quote)
        return '"' in pair and any(q in TYPOGRAPHIC_QUOTES for q in pair)


# --- Word packages ---------------------------------------------------------------------


def is_zip_package(data: bytes) -> bool:
    """True for a ZIP-based (Office Open XML) package such as a ``.docx``."""
    return data[:4] == _ZIP_MAGIC


def is_compound_file(data: bytes) -> bool:
    """True for a legacy binary compound file such as an old ``.doc``."""
    return data[:8] == _COMPOUND_FILE_MAGIC


_STORY_PART = re.compile(r"word/(?:(document)|(header)(\d*)|(footer)(\d*))\.xml")
_PARAGRAPH_END = re.compile(r"</w:p>")
_MARKUP = re.compile(r"<[^>]*>")


def _story_order(name: str) -> tuple[int, int, str] | None:
    hit = _STORY_PART.fullmatch(name)
    if hit is None:
        return None
    if hit.group(1):
        return (0, 0, name)
    rank, number = (1, hit.group(3)) if hit.group(2) else (2, hit.group(5))
    return (rank, int(number or 0), name)


def package_source_text(data: bytes) -> str:
    """Return the template text of a ``.docx``: body, then headers, then footers.

    Markup is stripped from the raw XML (a paragraph end becomes a newline) and entities
    are decoded. Styles, settings, numbering, comments and other parts are not read.

    Raises ``ValueError`` mentioning ``unreadable`` for anything that is not a readable
    ZIP package, so a caller can report it rather than call it clean.
    """
    if not is_zip_package(data):
        raise ValueError(
            "unreadable template: not a ZIP-based Word package; check is_zip_package() first"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            ranked = sorted(
                (order, name)
                for name in package.namelist()
                if (order := _story_order(name)) is not None
            )
            raw = [package.read(name).decode("utf-8", errors="replace") for _, name in ranked]
    except zipfile.BadZipFile as exc:
        raise ValueError(f"unreadable template: damaged ZIP package ({exc})") from exc
    return "\n".join(html.unescape(_MARKUP.sub("", _PARAGRAPH_END.sub("\n", xml))) for xml in raw)


# --- dialects --------------------------------------------------------------------------


class _Mustache:
    # {{ path }} or {{ path|filter }}; the filter may not span braces or lines.
    value = re.compile(rf"\{{\{{\s*(?P<path>{_DOTTED})\s*(?:\|(?P<filter>[^{{}}\n]*))?\}}\}}")
    # Block tags that matter for conditionals: if/each openers, else, and closers.
    block = re.compile(
        r"\{\{\s*(?:#(?P<opens>if|each)\s+(?P<arg>[^{}]*?)|(?P<alt>else)|/(?P<closes>if|each))"
        r"\s*\}\}"
    )
    damage = (
        # single opening brace, double closing:   { total }}
        re.compile(rf"(?<!\{{)\{{(?!\{{)\s*{_DOTTED}\s*(?:\|[^{{}}\n]*)?\}}\}}"),
        # double opening brace, single closing:   {{ total }
        re.compile(rf"\{{\{{\s*{_DOTTED}\s*(?:\|[^{{}}\n]*)?\}}(?!\}})"),
        # double opening brace, never closed on its line:   {{ total
        re.compile(rf"\{{\{{\s*{_DOTTED}[^}}\n]*$", re.M),
    )
    quoted_argument = re.compile(
        rf"^[^{_ANY_QUOTE}]*?:\s*(?P<lq>[{_ANY_QUOTE}])(?P<arg>.*)(?P<rq>[{_ANY_QUOTE}])\s*$"
    )

    def paths(self, text: str) -> list[str]:
        return [m["path"] for m in self.value.finditer(text) if m["path"] != "else"]

    def damaged(self, text: str) -> list[str]:
        spans: dict[tuple[int, int], str] = {}
        for pattern in self.damage:
            for m in pattern.finditer(text):
                spans.setdefault(m.span(), m.group(0).strip())
        return [frag for _, frag in sorted(spans.items())]

    def conditionals(self, text: str) -> list[Conditional]:
        found: list[list[Any]] = []
        open_blocks: list[tuple[str, int]] = []
        for m in self.block.finditer(text):
            if m["opens"] == "if":
                open_blocks.append(("if", len(found)))
                found.append([m["arg"].strip(), False])
            elif m["opens"] == "each":
                open_blocks.append(("each", -1))
            elif m["alt"]:
                if open_blocks and open_blocks[-1][0] == "if":
                    found[open_blocks[-1][1]][1] = True
            elif open_blocks and open_blocks[-1][0] == m["closes"]:
                open_blocks.pop()
        return [Conditional(guard, alt) for guard, alt in found]

    def filters(self, text: str) -> list[ValueFilter]:
        out = []
        for m in self.value.finditer(text):
            raw = m["filter"]
            if raw is None or m["path"] == "else":
                continue
            raw = raw.strip()
            quoted = self.quoted_argument.match(raw)
            if quoted:
                out.append(ValueFilter(quoted["arg"], quoted["lq"], quoted["rq"]))
            else:
                out.append(ValueFilter(raw, "", ""))
        return out

    def read_names(self, text: str) -> list[str]:
        located = [(m.start(), m["path"]) for m in self.value.finditer(text) if m["path"] != "else"]
        for m in self.block.finditer(text):
            if m["opens"]:
                located.extend((m.start(), name) for name in re.findall(_DOTTED, m["arg"]))
        return [name for _, name in sorted(located)]


class _MergeField:
    # MERGEFIELD name  or  MERGEFIELD "name"; a switch starts with a backslash.
    field = re.compile(r'\bMERGEFIELD\s+(?:"(?P<quoted>[^"\n]+)"|(?P<bare>[^\s"\\{}]+))')
    nameless = re.compile(r"\bMERGEFIELD(?=[ \t]*(?:\\|\}|$))[ \t]*(?:\\\S*)?", re.M)
    condition = re.compile(r'\bIF\s+\{?\s*MERGEFIELD\s+"?(?P<name>[^\s"\\{}]+)')

    def paths(self, text: str) -> list[str]:
        return [m["quoted"] or m["bare"] for m in self.field.finditer(text)]

    def damaged(self, text: str) -> list[str]:
        return [m.group(0).strip() for m in self.nameless.finditer(text)]

    def conditionals(self, text: str) -> list[Conditional]:
        return [Conditional(m["name"], False) for m in self.condition.finditer(text)]

    def filters(self, text: str) -> list[ValueFilter]:
        return []

    def read_names(self, text: str) -> list[str]:
        return self.paths(text)


_DIALECTS: dict[str, _Mustache | _MergeField] = {
    "mustache": _Mustache(),
    "mergefield": _MergeField(),
}


def _dialect(syntax: str) -> _Mustache | _MergeField:
    try:
        return _DIALECTS[syntax]
    except KeyError:
        known = ", ".join(sorted(_DIALECTS))
        raise ValueError(f"unknown template syntax {syntax!r}; known: {known}") from None


# --- public scans ----------------------------------------------------------------------


def placeholder_paths(text: str, syntax: str = "mustache") -> list[str]:
    """Paths of the well-formed value placeholders, in order, repeats kept.

    >>> placeholder_paths("{{ buyer.city }}, {{ amount|money }} {{#if paid}}ok{{/if}}")
    ['buyer.city', 'amount']
    """
    return _dialect(syntax).paths(text)


def damaged_placeholders(text: str, syntax: str = "mustache") -> list[str]:
    """Placeholder-like fragments whose brackets are damaged, so they would print verbatim.

    A line that starts with ``?{path}`` is the demo's conditional-line marker and is not
    reported.

    >>> damaged_placeholders("Due { invoice.due }} and {{ invoice.ref }")
    ['{ invoice.due }}', '{{ invoice.ref }']
    """
    return _dialect(syntax).damaged(text)


def conditional_blocks(text: str, syntax: str = "mustache") -> list[Conditional]:
    """One entry per conditional opener, in order, saying whether it has its own else.

    A conditional without an else prints nothing when false, the usual source of a label
    left without a value; the count gives a reviewer something to look at. An else inside
    a nested block belongs to that block, and a missing closer runs to the end.

    >>> conditional_blocks("{{#if a}}{{#if b}}x{{else}}y{{/if}}{{/if}}")
    [Conditional(guard='a', has_alternative=False), Conditional(guard='b', has_alternative=True)]
    """
    return _dialect(syntax).conditionals(text)


def value_filters(text: str, syntax: str = "mustache") -> list[ValueFilter]:
    """The filter of every value placeholder that has one, in order.

    >>> [f.text for f in value_filters('{{ due|date:"D-M-Y" }} {{ fee|money }}')]
    ['D-M-Y', 'money']
    """
    return _dialect(syntax).filters(text)


def top_level_names(text: str, syntax: str = "mustache") -> list[str]:
    """Distinct first path segments the template reads, in first-seen order.

    Value placeholders, loop collections and conditionals all count; ``this``, ``true``
    and ``false`` never do. Mustache has no named loop variable, so a field read inside a
    loop shows up as a name of its own.

    >>> top_level_names("{{#each lines}}{{qty}}{{/each}} {{ buyer.city }}")
    ['lines', 'qty', 'buyer']
    """
    seen: dict[str, None] = {}
    for path in _dialect(syntax).read_names(text):
        head = path.split(".", 1)[0]
        if head not in _NOT_DATA:
            seen.setdefault(head, None)
    return list(seen)


# --- schema checks ---------------------------------------------------------------------


def _through_arrays(node: Mapping[str, Any]) -> Mapping[str, Any]:
    while node.get("type") == "array" and isinstance(node.get("items"), Mapping):
        node = node["items"]
    return node


def schema_declares(schema: Mapping[str, Any], path: str) -> bool:
    """True when ``path`` (dot-separated) is reachable in a JSON schema.

    Arrays are stepped through to their ``items``. A node with no ``properties`` accepts
    every further segment when its ``additionalProperties`` is ``true`` or a schema, and
    none when it is absent or ``false``.

    >>> s = {"properties": {"buyer": {"properties": {"city": {"type": "string"}}}}}
    >>> schema_declares(s, "buyer.city"), schema_declares(s, "buyer.town")
    (True, False)
    """
    node = schema
    for segment in path.split("."):
        node = _through_arrays(node)
        children = node.get("properties")
        if isinstance(children, Mapping):
            if segment not in children:
                return False
            node = children[segment]
            continue
        extra = node.get("additionalProperties", False)
        return extra is True or isinstance(extra, Mapping)
    return True


def undeclared_paths(
    text: str,
    schema: Mapping[str, Any],
    syntax: str = "mustache",
    *,
    loop_aliases: Mapping[str, str] | None = None,
    ignore: Iterable[str] = (),
) -> list[str]:
    """Distinct placeholder paths the schema does not declare, in first-seen order.

    ``loop_aliases`` maps a loop variable to its collection path (``{"row": "sheet.rows"}``)
    so ``row.cell`` is checked as ``sheet.rows.cell``; the path is still reported as
    written. ``ignore`` suppresses a path given in either form.
    """
    aliases = loop_aliases or {}
    skip = set(ignore)
    out: dict[str, None] = {}
    for path in placeholder_paths(text, syntax):
        if path in out:
            continue
        head, dot, rest = path.partition(".")
        resolved = f"{aliases[head]}{dot}{rest}" if head in aliases else path
        if path in skip or resolved in skip:
            continue
        if not schema_declares(schema, resolved):
            out[path] = None
    return list(out)
