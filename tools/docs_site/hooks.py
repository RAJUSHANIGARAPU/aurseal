"""MkDocs hooks: keep every link on the site, and every link in the README, resolvable.

The site is built from ``docs/``, but its pages link to files elsewhere in the repository
(``../README.md``, ``../demo_sut/README.md``) and to directories of samples. On the
published site those targets do not exist, so a link to a repository file is rewritten
to the file on the repository host, after checking that the file exists. A link into
``reports/<example>/`` must name a directory under ``examples/``: the Pages workflow
publishes one report per example there.

Before the build, the README's links are checked the same way: a relative link must name
an existing file, a ``#fragment`` must match a heading, and a link into the published site
must name a page the build produces or an example report. Links to other hosts are not
fetched (the build is offline).

The pure functions take paths and text, so they are tested without MkDocs installed.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

_LINK_RE = re.compile(r"(!?\[[^\]]*\])\(([^)\s]+)((?:\s+\"[^\"]*\")?)\)")
_FENCE_RE = re.compile(r"^(```|~~~)")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
REPORTS_DIR = "reports"


def _outside_fences(text: str) -> list[tuple[bool, str]]:
    """Split ``text`` into (is_code, chunk) runs at fenced code block boundaries."""
    runs: list[tuple[bool, str]] = []
    code = False
    buffer: list[str] = []
    for line in text.splitlines(keepends=True):
        if _FENCE_RE.match(line.lstrip()):
            if not code:
                runs.append((False, "".join(buffer)))
                buffer = [line]
                code = True
            else:
                buffer.append(line)
                runs.append((True, "".join(buffer)))
                buffer = []
                code = False
            continue
        buffer.append(line)
    runs.append((code, "".join(buffer)))
    return runs


def github_slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces to ``-``."""
    text = re.sub(r"`|\*\*|__", "", heading.strip()).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def headings(text: str) -> set[str]:
    """Anchors of every Markdown heading outside fenced code, duplicates numbered."""
    seen: dict[str, int] = {}
    anchors = set()
    for is_code, chunk in _outside_fences(text):
        if is_code:
            continue
        for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", chunk, re.M):
            slug = github_slug(match.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def links(text: str) -> list[str]:
    """Link targets outside fenced code."""
    found = []
    for is_code, chunk in _outside_fences(text):
        if not is_code:
            found.extend(m.group(2) for m in _LINK_RE.finditer(chunk))
    return found


def _examples(root: Path) -> set[str]:
    base = root / "examples"
    return {p.name for p in base.iterdir() if p.is_dir()} if base.is_dir() else set()


def _site_page_exists(path: str, docs_dir: Path, examples: set[str]) -> bool:
    """Whether ``path`` (relative to the site root) is a page, file or report the site has."""
    parts = PurePosixPath(path).parts
    if not parts:
        return (docs_dir / "index.md").is_file()
    if parts[0] == REPORTS_DIR:
        return len(parts) >= 2 and parts[1] in examples
    stem = PurePosixPath(*parts)
    candidates = [
        docs_dir / stem / "index.md",
        docs_dir / stem / "README.md",
        docs_dir / f"{stem}.md",
        docs_dir / stem,
    ]
    return any(c.is_file() for c in candidates)


def readme_link_problems(readme: Path, root: Path, docs_dir: Path, site_url: str) -> list[str]:
    """Broken links in ``readme``: missing files, unknown anchors, site pages that won't exist."""
    text = readme.read_text(encoding="utf-8")
    own_anchors = headings(text)
    examples = _examples(root)
    site = site_url.rstrip("/") + "/"
    problems = []
    for target in links(text):
        if target.startswith(site):
            path = urlsplit(target).path[len(urlsplit(site).path) :]
            if not _site_page_exists(path.strip("/"), docs_dir, examples):
                problems.append(f"{target}: no such page on the site")
            continue
        if _SCHEME_RE.match(target):
            continue
        file_part, _, fragment = target.partition("#")
        if not file_part:
            if fragment not in own_anchors:
                problems.append(f"{target}: no such heading in {readme.name}")
            continue
        resolved = (readme.parent / file_part).resolve()
        if not resolved.exists():
            problems.append(f"{target}: no such file")
        elif fragment and resolved.suffix == ".md":
            if fragment not in headings(resolved.read_text(encoding="utf-8")):
                problems.append(f"{target}: no such heading in {file_part}")
    return problems


def rewrite_links(
    markdown: str,
    page: Path,
    docs_dir: Path,
    root: Path,
    repo_url: str,
    page_url: str = "",
    ref: str = "main",
) -> tuple[str, list[str]]:
    """Point links that leave ``docs_dir`` (or name a directory) at the repository host.

    A link into ``reports/`` is not a page MkDocs knows, so MkDocs leaves it as written;
    it is rewritten relative to ``page_url`` (the page's URL below the site root, such as
    ``kit/examples/``) so it lands on the report the Pages workflow publishes.
    Returns the rewritten Markdown and the links whose target does not exist.
    """
    docs_dir = docs_dir.resolve()
    root = root.resolve()
    examples = _examples(root)
    problems: list[str] = []

    def replace(match: re.Match[str]) -> str:
        label, target, title = match.groups()
        if _SCHEME_RE.match(target) or target.startswith(("#", "/")):
            return match.group(0)
        file_part, hash_, fragment = target.partition("#")
        resolved = (page.parent / file_part).resolve()
        if resolved.is_relative_to(docs_dir / REPORTS_DIR):
            parts = resolved.relative_to(docs_dir / REPORTS_DIR).parts
            if not parts or parts[0] not in examples:
                problems.append(f"{target}: no example of that name under examples/")
                return match.group(0)
            up = "../" * page_url.count("/")
            rest = "/".join(parts) + "/"
            return f"{label}({up}{REPORTS_DIR}/{rest}{hash_}{fragment}{title})"
        if resolved.is_relative_to(docs_dir) and resolved.is_file():
            return match.group(0)  # MkDocs validates links between its own pages
        if not resolved.is_relative_to(root) or not resolved.exists():
            problems.append(f"{target}: no such file in the repository")
            return match.group(0)
        kind = "tree" if resolved.is_dir() else "blob"
        relative = resolved.relative_to(root).as_posix()
        url = f"{repo_url.rstrip('/')}/{kind}/{ref}/{relative}{hash_}{fragment}"
        return f"{label}({url}{title})"

    out = []
    for is_code, chunk in _outside_fences(markdown):
        out.append(chunk if is_code else _LINK_RE.sub(replace, chunk))
    return "".join(out), problems


# ------------------------------------------------------------------ MkDocs entry points


def _root(config: Any) -> Path:
    return Path(config.config_file_path).resolve().parent


def on_pre_build(config: Any) -> None:
    from mkdocs.exceptions import PluginError

    root = _root(config)
    problems = readme_link_problems(
        root / "README.md", root, Path(config.docs_dir), config.site_url or ""
    )
    if problems:
        raise PluginError("README.md has broken links:\n  " + "\n  ".join(problems))


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:
    from mkdocs.exceptions import PluginError

    source = Path(page.file.abs_src_path)
    text, problems = rewrite_links(
        markdown,
        source,
        Path(config.docs_dir),
        _root(config),
        config.repo_url or "",
        page_url=page.url,
    )
    if problems:
        raise PluginError(f"{page.file.src_path} has broken links:\n  " + "\n  ".join(problems))
    return text
