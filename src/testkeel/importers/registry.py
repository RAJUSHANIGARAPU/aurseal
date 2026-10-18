"""Importer registry: find the right importer for a file and parse it.

The built-in importers are always available; plugins registered under the
``testkeel.importers`` entry-point group are added (a plugin may replace a built-in by
using the same name).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import IO

from testkeel.importers._build import ReportError
from testkeel.importers.cypress import CypressImporter
from testkeel.importers.junit import JUnitImporter
from testkeel.importers.k6 import K6Importer
from testkeel.importers.newman import NewmanImporter
from testkeel.importers.playwright import PlaywrightImporter
from testkeel.importers.upload import UploadImporter
from testkeel.model.v1 import TestRecord
from testkeel.plugin_api.v1 import IMPORTERS, Importer, discover

HEAD_BYTES = 4096
MIN_CONFIDENCE = 0.5
BUILTIN: dict[str, Importer] = {
    JUnitImporter.name: JUnitImporter(),
    PlaywrightImporter.name: PlaywrightImporter(),
    UploadImporter.name: UploadImporter(),
    CypressImporter.name: CypressImporter(),
    NewmanImporter.name: NewmanImporter(),
    K6Importer.name: K6Importer(),
}


def importers() -> dict[str, Importer]:
    """Built-in importers plus every installed plugin, by name."""
    return {**BUILTIN, **discover(IMPORTERS)}


def sniff(head: bytes) -> dict[str, float]:
    """Each importer's confidence that a file starting with ``head`` is its format."""
    return {name: imp.sniff(head) for name, imp in importers().items()}


def detect(head: bytes, minimum: float = MIN_CONFIDENCE) -> Importer:
    """The most confident importer; :class:`ReportError` when none reaches ``minimum``."""
    scores = sniff(head)
    available = importers()
    best = max(scores, key=lambda name: scores[name], default=None)
    if best is None or scores[best] < minimum:
        raise ReportError(f"unrecognised report format (tried: {', '.join(sorted(scores))})")
    return available[best]


def parse(stream: IO[bytes], format: str | None = None) -> Iterator[TestRecord]:
    """Records from an open binary stream; the format is sniffed unless named."""
    if format is not None:
        available = importers()
        if format not in available:
            raise ReportError(f"unknown format {format!r}; known: {sorted(available)}")
        return available[format].parse(stream)
    head = stream.read(HEAD_BYTES)
    stream.seek(0)
    return detect(head).parse(stream)


def parse_file(path: str | Path, format: str | None = None) -> list[TestRecord]:
    """Every record in the report at ``path``."""
    with open(path, "rb") as stream:
        return list(parse(stream, format))
