"""Per-template structural validation, manifest coverage, and rendered-document checks.

The pattern for a template estate (document templates, e-mail templates, any
merge-driven output):

1. **Coverage** — :func:`manifest_coverage`: every deployed template is
   described by a manifest entry (or explicitly quarantined), the quarantine
   holds no stale names, and names are unique. A template nobody described is a
   template nobody tests.
2. **Structure** — :func:`validate_template`: it parses, its delimiters are
   well-formed, its format strings are known and quoted straight, and every
   path it reads exists in the payload schema.
3. **Rendering** — :func:`check_rendered`: render each manifest entry with its
   payload and run every :mod:`oracles.document_findings` oracle on the text.

Each step yields plain findings, so a test parametrizes over templates and
asserts the list is empty — one red per broken template, not one per suite.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from oracles import document_findings, placeholder_scan


@dataclass(frozen=True)
class TemplateFinding:
    template: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.template}: [{self.kind}] {self.detail}"


@dataclass(frozen=True)
class ManifestCoverage:
    undescribed: list[str] = field(default_factory=list)
    stale_quarantine: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.undescribed or self.stale_quarantine or self.duplicates)


def manifest_coverage(
    deployed: Iterable[str],
    described: Iterable[str],
    *,
    quarantine: Iterable[str] = (),
    exempt_prefixes: Iterable[str] = (),
) -> ManifestCoverage:
    """Compare what is deployed with what the manifest describes.

    ``quarantine`` lists templates deliberately left undescribed; an entry that
    is no longer deployed, or that is now described, is stale and must go so the
    quarantine never hides more than it says. ``exempt_prefixes`` covers
    families that are append-only by design.
    """
    deployed = list(deployed)
    described_set = set(described)
    quarantine_set = set(quarantine)
    prefixes = tuple(exempt_prefixes)
    deployed_set = set(deployed)
    undescribed = sorted(
        n
        for n in deployed_set
        if n not in described_set and n not in quarantine_set and not n.startswith(prefixes)
    )
    stale = sorted(n for n in quarantine_set if n not in deployed_set or n in described_set)
    dupes = sorted(n for n, c in Counter(deployed).items() if c > 1)
    return ManifestCoverage(undescribed, stale, dupes)


def validate_template(
    name: str,
    content: bytes | str,
    *,
    syntax: str = "mustache",
    schema: Mapping[str, Any] | None = None,
    known_formats: Iterable[str] | None = None,
    loop_aliases: Mapping[str, str] | None = None,
) -> list[TemplateFinding]:
    """Structural findings for one template. Bytes are read as OOXML; str as plain text."""
    if isinstance(content, bytes):
        if not placeholder_scan.is_zip_package(content):
            kind = "legacy binary" if placeholder_scan.is_compound_file(content) else "unknown"
            return [
                TemplateFinding(name, "UNREADABLE", f"a {kind} file, so its text cannot be read")
            ]
        text = placeholder_scan.package_source_text(content)
    else:
        text = content

    out = [
        TemplateFinding(name, "BAD-DELIMITER", frag)
        for frag in placeholder_scan.damaged_placeholders(text, syntax)
    ]
    known = set(known_formats) if known_formats is not None else None
    for flt in placeholder_scan.value_filters(text, syntax):
        if flt.uses_typographic_quote:
            out.append(
                TemplateFinding(
                    name,
                    "CURLY-QUOTE",
                    f"the format '{flt.text}' is quoted with typographic quotes",
                )
            )
        if known is not None and flt.text not in known:
            out.append(TemplateFinding(name, "UNKNOWN-FILTER", flt.text))
    if schema is not None:
        out.extend(
            TemplateFinding(name, "UNDECLARED-PATH", path)
            for path in placeholder_scan.undeclared_paths(
                text, schema, syntax, loop_aliases=loop_aliases
            )
        )
    return out


@dataclass(frozen=True)
class RenderEntry:
    """One manifest entry: a template, the payload to render it with, and what must show."""

    template: str
    payload: Mapping[str, Any]
    pinned_text: Mapping[str, str] | tuple[str, ...] = field(default_factory=dict)
    must_show: tuple[str, ...] = ()
    section_headings: tuple[str, ...] = ()
    money_fields: tuple[str, ...] = ()


def check_rendered(
    entries: Iterable[RenderEntry],
    render: Callable[[str, Mapping[str, Any]], bytes | str],
    extract: Callable[[bytes], str] | None = None,
    *,
    locale: str = "nl_NL",
    currency_marker: str | None = None,
) -> dict[str, list[document_findings.Finding]]:
    """Render every entry and run every integrity oracle on the result.

    ``render`` returns the document (bytes) or its text; ``extract`` turns bytes
    into text (e.g. :func:`oracles.pdf_text.extract_text`).
    """
    results = {}
    for entry in entries:
        rendered = render(entry.template, entry.payload)
        if isinstance(rendered, bytes):
            if extract is None:
                raise ValueError("render returned bytes; pass extract= to turn them into text")
            rendered = extract(rendered)
        results[entry.template] = document_findings.all_findings(
            rendered,
            pinned_text=entry.pinned_text,
            must_show=entry.must_show,
            section_headings=entry.section_headings,
            money_fields=entry.money_fields,
            locale=locale,
            currency_marker=currency_marker,
        )
    return results
