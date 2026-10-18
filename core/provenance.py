"""
Which version of an external checkout did this run read?

Suites often assert against a second repository: a configuration repo, a shared
schema, an OpenAPI spec checked out next to this one. If the report does not say
which commit of it was read, two runs a week apart look identical and a red check
cannot be attributed to a change *there* rather than a change *here*. Locally it is
worse: the sibling checkout is often weeks stale, and nothing says so.

``CheckoutProvenance`` answers three questions for the report header:

* **where** — an explicit path (environment variable) is authoritative and is *not*
  allowed to fall back to the sibling directory, since that would silently describe
  some other checkout than the requested one;
* **ref** — ``not bound`` when nothing resolved (so the header distinguishes "read
  at main" from "asserted nothing"), ``pinned checkout`` when the caller states the
  working tree *is* the version, else the configured ref;
* **commit** — a commit passed in by CI first (CI containers often have no ``git``
  binary, so shelling out there returns nothing in exactly the place the answer
  matters), then ``git rev-parse`` in the checkout as the local fallback; 8 chars.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

NOT_BOUND = "not bound"
UNKNOWN = "unknown"


def _git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else UNKNOWN


def shorten(sha: str | None) -> str:
    if not sha or not sha.strip():
        return UNKNOWN
    return sha.strip()[:8]


@dataclass(frozen=True)
class CheckoutProvenance:
    """
    Provenance of one external checkout, configured by a variable prefix.

    With ``prefix="SPEC_REPO"`` it reads ``SPEC_REPO_PATH``, ``SPEC_REPO_REF``,
    ``SPEC_REPO_PINNED`` and ``SPEC_REPO_COMMIT``.
    """

    prefix: str
    default_sibling: str
    default_ref: str = "origin/main"
    marker_file: str | None = None  # a file that proves an explicit path is the right repo

    def _var(self, environ: Mapping[str, str], name: str) -> str:
        return environ.get(f"{self.prefix}_{name}", "").strip()

    def checkout(
        self, environ: Mapping[str, str] | None = None, cwd: Path | None = None
    ) -> Path | None:
        environ = os.environ if environ is None else environ
        explicit = self._var(environ, "PATH")
        if explicit:
            path = Path(explicit)
            is_repo = (path / ".git").exists()
            has_marker = bool(self.marker_file) and (path / self.marker_file).is_file()
            return path if is_repo or has_marker else None
        sibling = (cwd or Path.cwd()).parent / self.default_sibling
        return sibling if (sibling / ".git").exists() else None

    def _pinned(self, environ: Mapping[str, str]) -> bool:
        return self._var(environ, "PINNED").lower() in ("1", "true", "yes", "on")

    def ref(self, environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> str:
        environ = os.environ if environ is None else environ
        if self.checkout(environ, cwd) is None:
            return NOT_BOUND
        if self._pinned(environ):
            return "pinned checkout"
        return self._var(environ, "REF") or self.default_ref

    def commit(self, environ: Mapping[str, str] | None = None, cwd: Path | None = None) -> str:
        environ = os.environ if environ is None else environ
        checkout = self.checkout(environ, cwd)
        if checkout is None:
            return NOT_BOUND
        passed_in = self._var(environ, "COMMIT")
        if passed_in:
            return shorten(passed_in)
        rev = "HEAD" if self._pinned(environ) else (self._var(environ, "REF") or self.default_ref)
        return shorten(_git(checkout, "rev-parse", rev))

    def as_environment(
        self, label: str, environ: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """Rows for the Allure Environment panel."""
        return {f"{label}.Ref": self.ref(environ), f"{label}.Commit": self.commit(environ)}
