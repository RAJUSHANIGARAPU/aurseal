"""Canonical upload importer: a ``RunUpload`` JSON document, as the pytest plugin writes it.

The document is validated with the v1 model and its records are returned unchanged; a
document of another schema major is refused rather than guessed at.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import IO

from pydantic import ValidationError

from aurseal.importers._build import ReportError
from aurseal.model.v1 import SCHEMA_VERSION, RunUpload, TestRecord


class UploadImporter:
    name = "aurseal"

    def sniff(self, head: bytes) -> float:
        text = head.lstrip()
        if text.startswith(b"{") and b'"schema_version"' in text and b'"records"' in text:
            return 0.95
        return 0.0

    def parse(self, stream: IO[bytes]) -> Iterator[TestRecord]:
        try:
            document = json.load(stream)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReportError(f"not a aurseal upload document: {exc}") from exc
        if not isinstance(document, dict):
            raise ReportError("not a aurseal upload document: not a JSON object")
        version = str(document.get("schema_version") or "")
        if version.split(".", 1)[0] != SCHEMA_VERSION.split(".", 1)[0]:
            raise ReportError(
                f"upload schema_version {version!r} is not readable by this version "
                f"(reads {SCHEMA_VERSION.split('.', 1)[0]}.x)"
            )
        try:
            upload = RunUpload.model_validate(document)
        except ValidationError as exc:
            raise ReportError(f"invalid aurseal upload document: {exc}") from exc
        yield from upload.records
