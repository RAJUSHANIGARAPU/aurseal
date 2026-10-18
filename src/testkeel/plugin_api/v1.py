"""Plugin interface, version 1.

Three kinds of plugin, each discovered through its own entry-point group:

* ``testkeel.importers`` -> :class:`Importer` (``sniff`` + ``parse``)
* ``testkeel.result_types`` -> :class:`ResultType` (a name and a payload JSON Schema)
* ``testkeel.reporters`` -> :class:`Reporter` (render a run to a file)

A plugin package declares, in its ``pyproject.toml``::

    [project.entry-points."testkeel.importers"]
    k6 = "my_plugin.k6:K6Importer"

The entry point may name a class (instantiated without arguments) or an instance.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

from testkeel.model.v1 import RunUpload, TestRecord

IMPORTERS = "testkeel.importers"
RESULT_TYPES = "testkeel.result_types"
REPORTERS = "testkeel.reporters"


@runtime_checkable
class Importer(Protocol):
    """Turns one report file into test records."""

    name: str

    def sniff(self, head: bytes) -> float:
        """Confidence 0.0-1.0 that a file starting with ``head`` is this format."""
        ...

    def parse(self, stream: IO[bytes]) -> Iterator[TestRecord]:
        """Stream records out of an open binary file."""
        ...


@runtime_checkable
class ResultType(Protocol):
    """A non-functional result kind (performance, accessibility, ...)."""

    name: str

    def payload_schema(self) -> dict[str, Any]:
        """JSON Schema of the ``ResultTypePayload.payload`` this type accepts."""
        ...


@runtime_checkable
class Reporter(Protocol):
    """Renders a run to a file or directory."""

    name: str

    def render(self, upload: RunUpload, out: Path) -> Path:
        """Write the report under ``out``; return the main file written."""
        ...


_KINDS: dict[str, type[Any]] = {
    IMPORTERS: Importer,
    RESULT_TYPES: ResultType,
    REPORTERS: Reporter,
}


class PluginError(RuntimeError):
    """A plugin could not be loaded or does not implement its interface."""


def _instantiate(entry: EntryPoint, kind: type[Any]) -> Any:
    try:
        loaded = entry.load()
        plugin = loaded() if isinstance(loaded, type) else loaded
    except Exception as exc:  # a broken plugin must name itself, not crash the host
        raise PluginError(f"{entry.group}:{entry.name} failed to load: {exc}") from exc
    if not isinstance(plugin, kind):
        raise PluginError(f"{entry.group}:{entry.name} does not implement {kind.__name__}")
    return plugin


def discover(group: str) -> dict[str, Any]:
    """Every plugin registered under ``group``, by entry-point name (sorted).

    Raises :class:`PluginError` for an unknown group or a plugin that fails to load or
    does not implement the group's interface.
    """
    kind = _KINDS.get(group)
    if kind is None:
        raise PluginError(f"unknown plugin group {group!r}; known: {sorted(_KINDS)}")
    found = sorted(entry_points(group=group), key=lambda e: e.name)
    return {entry.name: _instantiate(entry, kind) for entry in found}


__all__ = [
    "IMPORTERS",
    "REPORTERS",
    "RESULT_TYPES",
    "Importer",
    "PluginError",
    "Reporter",
    "ResultType",
    "discover",
]
