"""
The locator registry as a store that is safe to heal into.

``ui/locators/*.json`` is loaded by every page object at construction, so a write
to it is never scoped to the test that triggered the heal. Three properties make
that write safe:

* **Atomic.** The new registry is written to a temp file in the same directory
  and swapped in with ``os.replace``. A reader — another xdist worker
  constructing a page object — sees the old file or the new one, never half of
  one.
* **Serialised, and read-modify-write under the lock.** Writers on one file take
  a per-path thread lock *and* an advisory file lock (``fcntl``, where the
  platform has it) and re-read the file inside the lock, so two heals of
  different keys from two processes both land instead of the second silently
  reverting the first.
* **Proposed, not applied, by default.** A heal goes to a side file,
  ``<name>.heals-pending.json``, holding the current entry, the proposal and the
  reason, and the committed registry is untouched. A person reviews the side
  file and runs ``apply_pending()`` (or sets ``HEAL_AUTOPERSIST=true`` to write
  heals straight into the registry). An automatic heal that rewrites a
  committed file is the kind of change a review should see before a run
  depends on it.

Each healed entry carries ``last_healed`` (UTC ISO timestamp) and
``healed_from`` (the entry it replaced, without its own metadata), so a diff of
the registry shows which entries a heal produced and what they were before.
These keys are ignored by the locator renderer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:  # advisory cross-process lock; absent on Windows
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

AUTOPERSIST_VARIABLE = "HEAL_AUTOPERSIST"
HEAL_METADATA_KEYS = ("last_healed", "healed_from")
_TRUE = {"1", "true", "yes", "on"}

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def autopersist_from_env(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return (environ.get(AUTOPERSIST_VARIABLE) or "").strip().lower() in _TRUE


def strip_heal_metadata(entry: Mapping) -> dict:
    """``entry`` without the keys a heal adds; what the renderer and comparisons use."""
    return {k: v for k, v in entry.items() if k not in HEAL_METADATA_KEYS}


def pending_path_for(path: Path) -> Path:
    return path.with_name(f"{path.stem}.heals-pending.json")


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Thread lock for this process, plus a file lock for other processes."""
    with _thread_lock(path):
        if fcntl is None:
            yield
            return
        digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
        lock_file = Path(tempfile.gettempdir()) / f"locator-store-{digest}.lock"
        with open(lock_file, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_atomically(path: Path, data: Mapping) -> None:
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        if path.exists():
            os.chmod(temp_name, path.stat().st_mode & 0o777)
        else:
            os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


@dataclass(frozen=True)
class HealWrite:
    key: str
    persisted: bool  # True: the registry was rewritten; False: proposed in the side file
    path: Path  # the file that was written
    entry: dict  # the entry as written, metadata included


class LocatorStore:
    def __init__(self, path: str | Path, *, autopersist: bool | None = None) -> None:
        self.path = Path(path)
        self.pending_path = pending_path_for(self.path)
        self.autopersist = autopersist_from_env() if autopersist is None else autopersist
        self._entries: dict | None = None

    # ── reading ──────────────────────────────────────────────────────────────

    def load(self) -> dict:
        """The registry as on disk; raises ``FileNotFoundError`` when there is none."""
        self._entries = _read(self.path)
        return dict(self._entries)

    @property
    def entries(self) -> dict:
        """In-memory view: the registry plus heals made through this store."""
        if self._entries is None:
            self.load()
        return dict(self._entries or {})

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def pending(self) -> dict:
        try:
            return _read(self.pending_path)
        except FileNotFoundError:
            return {}

    # ── writing ──────────────────────────────────────────────────────────────

    def update(
        self, key: str, entry: Mapping, *, reason: str = "", healed_at: datetime | None = None
    ) -> HealWrite:
        """
        Heal ``key`` to ``entry``. The in-memory view always changes; the
        registry file only with autopersist, otherwise the side file does.
        """
        stamp = (healed_at or datetime.now(tz=timezone.utc)).isoformat(timespec="seconds")
        with _locked(self.path):
            registry = _read(self.path)
            if key not in registry:
                raise KeyError(f"'{key}' is not in {self.path.name}")
            current = registry[key]
            healed = {
                **strip_heal_metadata(entry),
                "last_healed": stamp,
                "healed_from": strip_heal_metadata(current),
            }
            if self.autopersist:
                registry[key] = healed
                _write_atomically(self.path, registry)
                target, persisted = self.path, True
            else:
                proposals = self.pending()
                proposals[key] = {
                    "current": current,
                    "proposed": healed,
                    "reason": reason,
                    "proposed_at": stamp,
                }
                _write_atomically(self.pending_path, proposals)
                target, persisted = self.pending_path, False
        entries = self.entries
        entries[key] = healed
        self._entries = entries
        return HealWrite(key=key, persisted=persisted, path=target, entry=healed)

    def apply_pending(self, keys: list[str] | None = None) -> list[str]:
        """
        Move reviewed proposals into the registry (all, or just ``keys``) and drop
        them from the side file. A proposal whose ``current`` no longer matches the
        registry is skipped: someone changed the entry since, and the proposal was
        made against something that is gone.
        """
        applied: list[str] = []
        with _locked(self.path):
            proposals = self.pending()
            if not proposals:
                return applied
            registry = _read(self.path)
            for key in list(proposals):
                if keys is not None and key not in keys:
                    continue
                proposal = proposals[key]
                if registry.get(key) != proposal.get("current"):
                    continue
                registry[key] = proposal["proposed"]
                del proposals[key]
                applied.append(key)
            if applied:
                _write_atomically(self.path, registry)
                if proposals:
                    _write_atomically(self.pending_path, proposals)
                else:
                    self.pending_path.unlink(missing_ok=True)
        self._entries = None
        return applied
