"""Collect (never run) the suite in a subprocess and return each item's trace attributes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from traceability._collector import OUT_ENV

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Collection:
    items: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    returncode: int = 0

    @property
    def nodeids(self) -> list[str]:
        return [i["nodeid"] for i in self.items]


def collect(
    rootdir: Path,
    args: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> Collection:
    """``pytest --collect-only -p traceability._collector`` under ``rootdir``."""
    rootdir = Path(rootdir).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "items.json"
        run_env = {**os.environ, **(env or {}), OUT_ENV: str(out), "RESULTS_STORE": "off"}
        run_env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(REPO_ROOT), run_env.get("PYTHONPATH")) if p
        )
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "traceability._collector",
            "-p",
            "no:cacheprovider",
            *args,
        ]
        proc = subprocess.run(cmd, cwd=rootdir, env=run_env, capture_output=True, text=True)
        data = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    errors = list(data.get("errors") or [])
    if proc.returncode not in (0, 5):
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
        errors.append(f"pytest exited {proc.returncode}: " + " | ".join(tail))
    return Collection(list(data.get("items") or []), errors, proc.returncode)
