"""Rules for a generated assignment project, on top of the denylist and personal-data rules.

They apply to ``new-assignment`` and ``assignment check`` only, never to this repository's
own scan: a repository may well talk about other hosts, ship editor config or import
``aurseal``; a project handed to a reviewer may not. The caller selects them by passing a
:class:`ProjectRules` to :func:`aurseal.leakgate.run`.

* ``host.unlisted``  a URL (or a container image registry) whose host is not the declared
  target, a reserved or private name, a public demo target or a standard tooling host;
* ``trace.tool``     an assistant or editor config path (``.claude/``, ``.cursor*``,
  ``CLAUDE.md``, ``AGENTS.md``) or ``.copier-answers.yml`` without ``--updatable``;
* ``lock.index``     a URL in ``uv.lock`` that is not PyPI;
* ``standalone.import`` / ``standalone.dependency``  the project imports, or depends on,
  ``aurseal``.

Like every leak-gate rule they report a location and a rule id, never the matched text.
"""

from __future__ import annotations

import ast
import ipaddress
import re
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from aurseal import PREVIOUS_NAME

HOST = "host.unlisted"
TRACE = "trace.tool"
LOCK_INDEX = "lock.index"
STANDALONE_IMPORT = "standalone.import"
STANDALONE_DEPENDENCY = "standalone.dependency"
RULES = (HOST, TRACE, LOCK_INDEX, STANDALONE_IMPORT, STANDALONE_DEPENDENCY)

ANSWERS_FILE = ".copier-answers.yml"
#: The template source an ``--updatable`` project records; the only answers file allowed.
PUBLIC_SOURCE = "https://github.com/RAJUSHANIGARAPU/aurseal"
#: The same repository before it was renamed; projects made until then record it.
PREVIOUS_PUBLIC_SOURCE = "https://github.com/RAJUSHANIGARAPU/wiki_project"
ALLOW_HOSTS_PATH = Path(".config") / "aurseal" / "allow-hosts.txt"
#: Read when ALLOW_HOSTS_PATH is absent: a deprecated fallback for one release.
PREVIOUS_ALLOW_HOSTS_PATH = Path(".config") / PREVIOUS_NAME / "allow-hosts.txt"
TOOL_PACKAGE = "aurseal"

#: Public demo targets the assignment types and examples point at.
DEMO_HOSTS = frozenset(
    {
        "saucedemo.com",
        "restful-booker.herokuapp.com",
        "the-internet.herokuapp.com",
        "reqres.in",
        "petstore.swagger.io",
        "petstore3.swagger.io",
        "countries.trevorblades.com",
        "rickandmortyapi.com",
        "jsonplaceholder.typicode.com",
        "httpbin.org",
        "quickpizza.grafana.com",
        "test.k6.io",
        "www.w3.org",
        "dequeuniversity.com",
    }
)
#: Package registries, container registries and the documentation generated files link to.
TOOLING_HOSTS = frozenset(
    {
        "pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "npmjs.com",
        "github.com",
        "mcr.microsoft.com",
        "ghcr.io",
        "docker.io",
        "docker.com",
        "quay.io",
        "playwright.dev",
        "astral.sh",
        "python.org",
        "pytest.org",
        "pydantic.dev",
        "python-httpx.org",
        "json-schema.org",
        "allurereport.org",
        "qameta.io",
        "k6.io",
        "grafana.com",
        "locust.io",
        "deque.com",
        "appium.io",
        "webdriver.io",
        "nodejs.org",
        "stoplight.io",
        "copier.readthedocs.io",
        "pre-commit.com",
        "shields.io",
    }
)
_RESERVED_SUFFIXES = (".example", ".test", ".invalid", ".localhost", ".local", ".internal")
_RESERVED_HOSTS = frozenset(
    {"localhost", "example.com", "example.org", "example.net", "host.docker.internal"}
)
_LOCK_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org"})

_URL_RE = re.compile(
    r"(?i)\b(?:https?|wss?|ftp|git|ssh)://(?:[^\s/@'\"<>]*@)?(\[[0-9a-f:.]+\]|[a-z0-9.-]+)"
)
# A container image with a registry host: `FROM host/...`, `image: host/...`.
_IMAGE_RE = re.compile(
    r"(?i)(?:^\s*FROM\s+(?:--platform=\S+\s+)?|\b(?:image|container):\s*[\"']?)"
    r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d+)?/"
)
_TRACE_NAMES = frozenset({".claude", "CLAUDE.md", "AGENTS.md"})
_REQUIREMENT_RE = re.compile(rf"""(?ix)(?:^\s*|["']\s*){TOOL_PACKAGE}\s*(?:$|[\[<>=!~;@\s"'])""")
_LOCK_NAME_RE = re.compile(rf'(?i)^\s*name\s*=\s*"{TOOL_PACKAGE}"\s*$')


def _within(host: str, names: Iterable[str]) -> bool:
    return any(host == name or host.endswith("." + name) for name in names)


def host_of(url: str) -> str | None:
    """The lower-case host of an http(s) URL, or None."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def load_allow_hosts(home: Path | None) -> frozenset[str]:
    """Extra public hosts from ``~/.config/aurseal/allow-hosts.txt`` (one per line), or
    from the same file under the package's previous name when that is the only one."""
    if home is None:
        return frozenset()
    path = home / ALLOW_HOSTS_PATH
    if not path.is_file() and (home / PREVIOUS_ALLOW_HOSTS_PATH).is_file():
        path = home / PREVIOUS_ALLOW_HOSTS_PATH
        warnings.warn(
            f"~/{PREVIOUS_ALLOW_HOSTS_PATH.as_posix()} is deprecated; "
            f"move it to ~/{ALLOW_HOSTS_PATH.as_posix()}",
            DeprecationWarning,
            stacklevel=2,
        )
    try:
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    lines = (line.split("#", 1)[0].strip().lower() for line in text.splitlines())
    return frozenset(line for line in lines if line)


def answers_point_at_public_source(text: str) -> bool:
    """True when an answers file records the public repository as its template source."""
    return any(
        line.split(":", 1)[0].strip() == "_src_path"
        and line.split(":", 1)[1].strip(" '\"") in (PUBLIC_SOURCE, PREVIOUS_PUBLIC_SOURCE)
        for line in text.splitlines()
        if ":" in line
    )


@dataclass(frozen=True)
class ProjectRules:
    """The generated-project rule set. ``hosts``: declared targets plus user extras."""

    hosts: frozenset[str] = frozenset()
    updatable: bool = False

    @classmethod
    def for_project(
        cls,
        urls: Iterable[str | None] = (),
        extra_hosts: Iterable[str] = (),
        home: Path | None = None,
        updatable: bool = False,
    ) -> ProjectRules:
        hosts = {h for h in (host_of(u) for u in urls if u) if h}
        hosts.update(h.strip().lower() for h in extra_hosts if h.strip())
        hosts.update(load_allow_hosts(home))
        return cls(frozenset(hosts), updatable)

    # ------------------------------------------------------------------ hosts

    def host_allowed(self, host: str) -> bool:
        host = host.strip("[]").rstrip(".").lower()
        if not host or "." not in host and ":" not in host:
            return True  # a single-label name (a compose service, a placeholder) is not public
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return not address.is_global
        if host in _RESERVED_HOSTS or host.endswith(_RESERVED_SUFFIXES):
            return True
        if _within(host, ("example.com", "example.org", "example.net")):
            return True
        return _within(host, self.hosts | DEMO_HOSTS | TOOLING_HOSTS)

    # ------------------------------------------------------------------ paths

    def path(self, relative: str, text: str | None = None) -> list[tuple[int, str]]:
        """``(depth, rule)`` for a path that is itself a trace; depth = parts to report."""
        parts = PurePosixPath(relative).parts
        for depth, part in enumerate(parts, start=1):
            if part in _TRACE_NAMES or part.startswith(".cursor"):
                return [(depth, TRACE)]
        if parts and parts[-1] == ANSWERS_FILE:
            allowed = self.updatable or (text is not None and answers_point_at_public_source(text))
            if not allowed:
                return [(len(parts), TRACE)]
        return []

    # ------------------------------------------------------------------ content

    def content(self, relative: str, text: str) -> list[tuple[int, str]]:
        """``(line, rule)`` hits in one file's text."""
        path = PurePosixPath(relative)
        if path.parts and path.parts[0] == ".task":
            return []  # the brief is git-ignored by design; it never travels
        hits: list[tuple[int, str]] = []
        lines = text.splitlines()
        if path.name == "uv.lock":
            for number, line in enumerate(lines, start=1):
                if any(not _within(h, _LOCK_HOSTS) for h in _hosts(line)):
                    hits.append((number, LOCK_INDEX))
                if _LOCK_NAME_RE.match(line):
                    hits.append((number, STANDALONE_DEPENDENCY))
            return hits
        for number, line in enumerate(lines, start=1):
            if any(not self.host_allowed(h) for h in _hosts(line)):
                hits.append((number, HOST))
        if path.suffix == ".py":
            hits.extend((number, STANDALONE_IMPORT) for number in _tool_imports(text))
        elif path.name == "pyproject.toml" or (
            path.name.startswith("requirements") and path.suffix in {".txt", ".in"}
        ):
            hits.extend(
                (number, STANDALONE_DEPENDENCY)
                for number, line in enumerate(lines, start=1)
                if not line.lstrip().startswith("#") and _REQUIREMENT_RE.search(line)
            )
        return hits


def _hosts(line: str) -> list[str]:
    found = []
    for match in _URL_RE.finditer(line):
        host = match.group(1).strip(".").lower()
        if any(ch.isalnum() for ch in host):
            found.append(host)
    found.extend(m.group(1).lower() for m in _IMAGE_RE.finditer(line))
    return found


def _tool_imports(text: str) -> list[int]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []  # the self-check reports a file that does not parse
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        if any(name.split(".", 1)[0] == TOOL_PACKAGE for name in names):
            lines.append(node.lineno)
    return lines


__all__ = [
    "ANSWERS_FILE",
    "PUBLIC_SOURCE",
    "RULES",
    "ProjectRules",
    "answers_point_at_public_source",
    "host_of",
    "load_allow_hosts",
]
