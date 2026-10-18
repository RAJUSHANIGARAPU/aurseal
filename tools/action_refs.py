"""Every GitHub Action this repository references resolves to a real tag, branch or commit.

``python -m tools.action_refs`` scans the repository's workflows, the assignment templates and
the examples for ``uses: owner/repo@ref`` and asks the GitHub API whether each ref exists.
It exits 1 listing the refs that do not resolve, and 2 when the API cannot be reached, so an
outage never reads as a pass. Generated projects copy the template workflows, so a ref that
does not exist breaks every project the kit scaffolds, not only this repository's CI.

``GITHUB_TOKEN`` is used when set (CI); without it the anonymous rate limit applies.
"""

from __future__ import annotations

import http.client
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

ROOTS = (".github/workflows", "src/aurseal/templates", "examples")
SUFFIXES = (".yml", ".yaml", ".jinja", ".part")
USES = re.compile(
    r"uses:\s*['\"]?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/[^@\s'\"]*)?@([A-Za-z0-9_.-]+)"
)
SHA = re.compile(r"[0-9a-f]{40}")

Fetch = Callable[[str], bool | None]
"""Returns True when the API path exists, False on 404, None when the API is unreachable."""


def references(root: Path, roots: Iterable[str] = ROOTS) -> set[tuple[str, str]]:
    """Every (owner/repo, ref) named by a ``uses:`` line under ``roots``."""
    found: set[tuple[str, str]] = set()
    for sub in roots:
        base = root / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.name.endswith(SUFFIXES) and ".venv" not in path.parts:
                found.update(USES.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found


def api_fetch(token: str | None) -> Fetch:
    """Ask api.github.com over HTTPS; the host is fixed, so no other scheme or host is possible."""

    def fetch(api_path: str) -> bool | None:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "aurseal-action-refs"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPSConnection("api.github.com", timeout=20)
        try:
            connection.request("GET", f"/{api_path}", headers=headers)
            response = connection.getresponse()
            response.read()
        except (OSError, http.client.HTTPException):
            return None
        finally:
            connection.close()
        if response.status == 200:
            return True
        return False if response.status in (404, 422) else None

    return fetch


def resolves(repo: str, ref: str, fetch: Fetch) -> bool | None:
    """True when ``ref`` is a commit, tag or branch of ``repo``; None when it cannot be told."""
    if SHA.fullmatch(ref):
        return fetch(f"repos/{repo}/commits/{ref}")
    tag = fetch(f"repos/{repo}/git/ref/tags/{ref}")
    if tag is not False:
        return tag
    return fetch(f"repos/{repo}/git/ref/heads/{ref}")


def main(argv: Sequence[str] | None = None, fetch: Fetch | None = None) -> int:
    root = Path(argv[0]) if argv else Path(".")
    fetch = fetch or api_fetch(os.environ.get("GITHUB_TOKEN"))
    missing: list[str] = []
    unknown: list[str] = []
    refs = sorted(references(root))
    for repo, ref in refs:
        result = resolves(repo, ref, fetch)
        if result is False:
            missing.append(f"{repo}@{ref}")
        elif result is None:
            unknown.append(f"{repo}@{ref}")
    for item in missing:
        print(f"does not exist: {item}")
    for item in unknown:
        print(f"could not check: {item}")
    if missing:
        return 1
    if unknown:
        return 2
    print(f"action refs: {len(refs)} checked, all resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
