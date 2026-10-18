"""Committed baseline screenshots, one directory per browser and platform.

``visual_baselines/<browser>-<platform>/<name>.png``. Fonts rasterise differently per
operating system, so a baseline is only comparable on the platform that took it. A 0-byte
or undecodable file is treated as missing, never as a baseline.

Approving copies a reviewed actual over the baseline and appends a record (name, browser,
platform, sha256, run id, time) to ``visual_baselines/approvals.jsonl``. The file change
still lands through a commit and its review; nothing approves automatically.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from checks._sink import REPO_ROOT
from checks.visual.compare import decode

DEFAULT_ROOT = REPO_ROOT / "visual_baselines"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def platform_key() -> str:
    return {"darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux")


def check_name(name: str) -> str:
    if not _NAME.match(name) or ".." in name:
        raise ValueError(f"not a usable snapshot name: {name!r}")
    return name


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BaselineStore:
    def __init__(
        self, root: Path | str | None = None, browser: str = "chromium", platform: str | None = None
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self.browser = browser
        self.platform = platform or platform_key()

    @property
    def directory(self) -> Path:
        return self.root / f"{self.browser}-{self.platform}"

    def path(self, name: str) -> Path:
        return self.directory / f"{check_name(name)}.png"

    def load(self, name: str) -> bytes | None:
        path = self.path(name)
        if not path.is_file():
            return None
        data = path.read_bytes()
        return data if decode(data) is not None else None

    def write(self, name: str, data: bytes) -> Path:
        if decode(data) is None:
            raise ValueError(f"refusing to store an empty or undecodable baseline for {name!r}")
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def approve(self, name: str, actual: Path, run_id: str | None = None) -> str:
        """Copy a reviewed actual over the baseline; returns the new baseline's sha256."""
        data = Path(actual).read_bytes()
        self.write(name, data)
        digest = sha256(data)
        record = {
            "name": name,
            "browser": self.browser,
            "platform": self.platform,
            "sha256": digest,
            "run_id": run_id,
            "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "approvals.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return digest
