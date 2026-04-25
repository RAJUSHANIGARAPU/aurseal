"""Versioned contract storage on the local filesystem.

Directory layout:
    contracts/
    └── {consumer}___{provider}/
        ├── index.json        — version history (latest first)
        ├── v1.0.0.json       — immutable snapshot
        ├── v1.1.0.json
        └── latest.json       — always a copy of the most recent version

The triple-underscore separator (`___`) avoids ambiguity with names that
contain single or double underscores.

All writes are atomic (write-to-temp → rename) where possible so that
concurrent CI runs don't produce corrupted contract files.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from contract_testing.models import Contract

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_INDEX_FILE = "index.json"
_LATEST_FILE = "latest.json"


class ContractStore:
    """Stores and retrieves versioned contracts on disk."""

    def __init__(self, contracts_dir: Path) -> None:
        self._root = contracts_dir
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, contract: Contract) -> Path:
        """Persist a contract under its version string. Returns the file path."""
        slot = self._slot(contract.consumer, contract.provider)
        slot.mkdir(parents=True, exist_ok=True)

        version_file = slot / f"v{contract.version}.json"
        self._atomic_write(version_file, contract.to_json())

        # Update latest
        self._atomic_write(slot / _LATEST_FILE, contract.to_json())

        # Update index
        self._update_index(slot, contract)
        logger.info("[contract] saved %s v%s", self._pair_name(contract), contract.version)
        return version_file

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load_latest(self, consumer: str, provider: str) -> Contract | None:
        """Return the most recent contract for this consumer/provider pair."""
        latest = self._slot(consumer, provider) / _LATEST_FILE
        if not latest.exists():
            return None
        try:
            return Contract.from_json(latest.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.debug("[contract] failed to load latest for %s/%s", consumer, provider)
            return None

    def load_version(self, consumer: str, provider: str, version: str) -> Contract | None:
        """Return a specific version of a contract."""
        path = self._slot(consumer, provider) / f"v{version}.json"
        if not path.exists():
            return None
        try:
            return Contract.from_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def list_versions(self, consumer: str, provider: str) -> list[str]:
        """Return all stored versions, newest first."""
        index = self._load_index(self._slot(consumer, provider))
        return [entry["version"] for entry in index]

    def exists(self, consumer: str, provider: str) -> bool:
        return (self._slot(consumer, provider) / _LATEST_FILE).exists()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _slot(self, consumer: str, provider: str) -> Path:
        return self._root / f"{consumer}___{provider}"

    @staticmethod
    def _pair_name(contract: Contract) -> str:
        return f"{contract.consumer}/{contract.provider}"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        dir_ = path.parent
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _load_index(self, slot: Path) -> list[dict]:
        index_path = slot / _INDEX_FILE
        if not index_path.exists():
            return []
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _update_index(self, slot: Path, contract: Contract) -> None:
        index = self._load_index(slot)
        # Remove existing entry for this version (idempotent save)
        index = [e for e in index if e.get("version") != contract.version]
        index.insert(
            0,
            {
                "version": contract.version,
                "created_at": contract.created_at or datetime.now(tz=timezone.utc).isoformat(),
                "consumer": contract.consumer,
                "provider": contract.provider,
            },
        )
        self._atomic_write(slot / _INDEX_FILE, json.dumps(index, indent=2))
