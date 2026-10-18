"""A throwaway git repository for the leak-gate tests: synthetic content only."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    if shutil.which("git") is None:
        pytest.skip("needs the git CLI")
    root = tmp_path / "repo"
    root.mkdir()
    r = Repo(root)
    r.git("init", "-q", "-b", "main")
    r.git("config", "user.name", "Test")
    r.git("config", "user.email", "test@example.invalid")
    r.git("config", "commit.gpgsign", "false")
    r.write("README.md", "hello\n")
    r.commit("init")
    return r
