"""Attach evidence to the running test without touching the database.

``attach(request, "trace", path)`` appends ``("evidence", {...})`` to the test's
``user_properties``. pytest (and xdist, for workers) carries user properties to the
controller with every report, where the recorder turns them into ``evidence`` rows. That
keeps the browser layer free of database access and safe on any worker.

The store holds links, never blobs: ``uri`` is relative to ``$RESULTS_ARTIFACT_ROOT``
when the file lives under it (default: the current directory), else absolute; an
``http(s)`` URL is kept as given.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from results._util import ARTIFACT_ROOT_ENV, utc_now
from results.models import EVIDENCE_KINDS

EVIDENCE_PROPERTY = "evidence"
HASH_LIMIT = 256 * 1024 * 1024  # files bigger than this are linked without a checksum


def _artifact_root() -> Path:
    return Path(os.environ.get(ARTIFACT_ROOT_ENV) or Path.cwd()).resolve()


def describe(kind: str, path: str | Path) -> dict[str, Any]:
    """The evidence payload for ``path``: uri, sha256 and size when the file exists."""
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind {kind!r}; one of {', '.join(EVIDENCE_KINDS)}")
    text = str(path)
    if text.startswith(("http://", "https://")):
        return {"kind": kind, "uri": text, "sha256": None, "bytes": None, "created_at": utc_now()}
    target = Path(path).resolve()
    root = _artifact_root()
    try:
        uri = target.relative_to(root).as_posix()
    except ValueError:
        uri = target.as_posix()
    sha = size = None
    if target.is_file():
        size = target.stat().st_size
        if size <= HASH_LIMIT:
            digest = hashlib.sha256()
            with target.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha = digest.hexdigest()
    return {"kind": kind, "uri": uri, "sha256": sha, "bytes": size, "created_at": utc_now()}


def attach(item_or_request: Any, kind: str, path: str | Path) -> None:
    """Record evidence for the current test (worker-safe; never raises on a missing file)."""
    node = getattr(item_or_request, "node", item_or_request)
    props = getattr(node, "user_properties", None)
    if props is None:
        raise TypeError("attach() needs a pytest item or the request fixture")
    props.append((EVIDENCE_PROPERTY, describe(kind, path)))


__all__ = ["EVIDENCE_PROPERTY", "attach", "describe"]
