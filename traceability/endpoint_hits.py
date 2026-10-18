"""Runtime evidence for the coverage-gap report: which API operations a run called.

Opt-in. With ``ENDPOINT_HITS=true`` (or ``ENDPOINT_HITS_FILE`` set) every
``api.clients.http_engine.HttpEngine`` appends ``{method, path, status, nodeid}`` per
response to ``reports/traceability/endpoint-hits.jsonl`` (one ``O_APPEND`` write per line,
xdist-safe). A hit proves an operation was *called*, not that its behaviour was
asserted: the gap report labels it "exercised", never "covered".

Stdlib only: the HTTP engine imports this module.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ENABLED_ENV = "ENDPOINT_HITS"
FILE_ENV = "ENDPOINT_HITS_FILE"
DEFAULT_FILE = Path("reports") / "traceability" / "endpoint-hits.jsonl"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_PARAM = re.compile(r"\{[^/{}]*\}|:[A-Za-z_]\w*|\{\{[^/{}]*\}\}")


@dataclass(frozen=True)
class EndpointHit:
    method: str
    path: str
    status: int | None = None
    nodeid: str = ""


def enabled() -> bool:
    if os.environ.get(FILE_ENV, "").strip():
        return True
    return os.environ.get(ENABLED_ENV, "").strip().lower() in _TRUTHY


def hits_path() -> Path:
    return Path(os.environ.get(FILE_ENV, "").strip() or DEFAULT_FILE)


def record_hit(method: str, url: str, status: int | None, *, path: Path | None = None) -> None:
    """Append one hit; never raises (evidence must not change a test's outcome)."""
    current = os.environ.get("PYTEST_CURRENT_TEST", "")
    nodeid = re.sub(r"\s+\((setup|call|teardown)\)$", "", current)
    entry = {
        "method": method.upper(),
        "path": urlsplit(url).path or "/",
        "status": status,
        "nodeid": nodeid,
    }
    target = path or hits_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(entry) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass


def maybe_attach(engine) -> None:
    """Register the recorder on an HTTP engine when hits are enabled."""
    if enabled():
        engine.observe(
            lambda method, url, response: record_hit(method, url, getattr(response, "status", None))
        )


def read_hits(path: Path | None = None) -> list[EndpointHit]:
    target = path or hits_path()
    out: list[EndpointHit] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            data = json.loads(line)
            out.append(
                EndpointHit(
                    str(data["method"]).upper(),
                    str(data["path"]),
                    data.get("status"),
                    str(data.get("nodeid") or ""),
                )
            )
        except (ValueError, KeyError, TypeError):
            continue
    return out


def normalise(path: str) -> str:
    """A template with every parameter spelled ``{}`` and no trailing slash."""
    text = _PARAM.sub("{}", path.strip())
    return text.rstrip("/") or "/"


def _regex(template: str) -> re.Pattern[str]:
    parts = normalise(template).split("{}")
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "/?$")


def match_template(path: str, templates: Iterable[str]) -> str | None:
    """The template ``path`` instantiates; the most literal wins (``/a/me`` over ``/a/{id}``)."""
    best: tuple[int, str] | None = None
    for template in templates:
        if _regex(template).match(path):
            literal = len(normalise(template).replace("{}", ""))
            if best is None or literal > best[0]:
                best = (literal, template)
    return best[1] if best else None


def iter_matched(
    hits: Iterable[EndpointHit], operations: Iterable[tuple[str, str]]
) -> Iterator[tuple[EndpointHit, str]]:
    """``(hit, "METHOD template")`` for every hit that matches an operation."""
    by_method: dict[str, list[str]] = {}
    for method, template in operations:
        by_method.setdefault(method.upper(), []).append(template)
    for hit in hits:
        found = match_template(hit.path, by_method.get(hit.method, ()))
        if found is not None:
            yield hit, f"{hit.method} {found}"
