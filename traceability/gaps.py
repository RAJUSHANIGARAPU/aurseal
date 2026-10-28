"""Coverage gaps: what a source says should be tested, against what the suite shows.

Deterministic, no model calls. *Sources* list what should be covered; *evidence* shows
what is:

=====================  ========================================================
OpenApiSource          one item per operation (``GET /api/customers/{id}``)
SpecSource             one item per ``## Scenario`` in ``specs/*.md``
PostmanSource          one item per request in a collection (v2.x)
RequirementSource      one item per active requirement in the registry
=====================  ========================================================

OpenAPI documents and Postman collections may be files or http(s) URLs.

Evidence, strongest first:

* **static** — ``@pytest.mark.covers("GET /api/customers/{id}")`` or
  ``covers("spec:subscription#cancel-refunds")`` on a test, or a requirement link (a
  requirement item, or a spec scenario whose Tags name the requirement);
* **runtime** — endpoint hits recorded during a run: the item is *exercised*, which
  proves a call, not an assertion.

Matching is by explicit tag or requirement link only. Fuzzy title matching is not done on
purpose: a false "covered" is worse than a visible gap.

The CI step uses a **ratchet**: ``traceability/gap-baseline.json`` lists accepted gaps. A
new gap fails the step, and so does a baseline entry that is no longer a gap ("stale
baseline entry, delete it"), so the baseline can only shrink (``--allow-stale`` turns the
second rule into a reminder). ``--write-baseline`` is the only way to rewrite it, in a
reviewed pull request. The report's JSON carries ``ratchet: {new, closed, passed}``, which
the release verdict reads.

Two report-only views, never read by the ratchet: gaps **per group** (``--groups``: group
name -> source-item prefixes or ``tag:<requirement>``), and the gaps of a **frozen ref**
(``--frozen-ref``: the same sources as they are at a git ref, e.g. a release tag).
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from traceability._util import read_json, utc_now, validate
from traceability.endpoint_hits import EndpointHit, iter_matched, normalise
from traceability.requirements import Namespace, Registry

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
MAX_DOC_BYTES = 5 * 1024 * 1024
URL_TIMEOUT_S = 15
COVERED, COVERED_NOT_PASSING, EXERCISED, GAP = "covered", "covered_not_passing", "exercised", "gap"
DEFAULT_BASELINE = Path("traceability") / "gap-baseline.json"
_SCENARIO = re.compile(r"^##\s+Scenario\s*:?\s*(.+?)\s*$", re.IGNORECASE)
_TAGS = re.compile(r"^###\s+Tags\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,3}\s")


class GapSourceError(ValueError):
    """A source that cannot be read, or that lists nothing (never "zero gaps")."""


@dataclass(frozen=True)
class SourceItem:
    source: str
    key: str
    title: str
    requirements: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.source}:{self.key}"


class GapSource(Protocol):
    name: str

    def items(self) -> Iterable[SourceItem]: ...


def operation_key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def is_url(location: str | Path) -> bool:
    return isinstance(location, str) and location.startswith(("http://", "https://"))


def location(text: str) -> str | Path:
    """An http(s) URL stays a string; anything else is a local path."""
    return text if is_url(text) else Path(text)


def read_document(source: str | Path) -> Any:
    """JSON from a file, or from an http(s) URL (capped at ``MAX_DOC_BYTES``).

    A file that cannot be read gives ``None``, like :func:`read_json`; a URL that cannot be
    fetched raises :class:`GapSourceError`, because a source that was not read is not one
    with no items.
    """
    if not is_url(source):
        return read_json(Path(source))
    import urllib.error
    import urllib.request

    try:
        # the scheme is http(s), checked by is_url above
        with urllib.request.urlopen(str(source), timeout=URL_TIMEOUT_S) as response:  # noqa: S310
            body = response.read(MAX_DOC_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GapSourceError(f"{source}: could not be fetched ({exc})") from None
    if len(body) > MAX_DOC_BYTES:
        raise GapSourceError(f"{source}: larger than {MAX_DOC_BYTES} bytes")
    try:
        return json.loads(body)
    except ValueError:
        return None


@dataclass
class OpenApiSource:
    path: Path | str  # a file, or an http(s) URL
    name: str = "openapi"

    def items(self) -> list[SourceItem]:
        data = read_document(self.path)
        if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
            raise GapSourceError(f"{self.path}: not an OpenAPI document with paths")
        out = []
        for route, ops in sorted(data["paths"].items()):
            for method, op in sorted((ops or {}).items()):
                if method.lower() not in HTTP_METHODS:
                    continue
                summary = (op or {}).get("summary") or (op or {}).get("operationId") or ""
                out.append(SourceItem(self.name, operation_key(method, route), summary))
        if not out:
            raise GapSourceError(f"{self.path}: lists no operation")
        return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass
class SpecSource:
    directory: Path
    name: str = "spec"
    namespace: Namespace | None = None

    def items(self) -> list[SourceItem]:
        ns = self.namespace or Namespace.from_env()
        out = []
        for path in sorted(Path(self.directory).glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            for title, reqs in scenarios(path.read_text(encoding="utf-8"), ns):
                out.append(SourceItem(self.name, f"{path.stem}#{_slug(title)}", title, reqs))
        if not out:
            raise GapSourceError(f"{self.directory}: no '## Scenario' in any spec file")
        return out


def scenarios(text: str, ns: Namespace) -> list[tuple[str, tuple[str, ...]]]:
    """``(title, requirement ids from its ### Tags)`` per ``## Scenario`` heading."""
    found: list[tuple[str | None, list[str]]] = []
    in_tags = False
    for line in text.splitlines():
        match = _SCENARIO.match(line)
        if match:
            found.append((match.group(1), []))
            in_tags = False
        elif _TAGS.match(line):
            in_tags = True
        elif _HEADING.match(line):
            in_tags = False
            if line.startswith("## ") and found and found[-1][0] is not None:
                found.append((None, []))  # a non-scenario section closes the scenario
        elif in_tags and found and found[-1][0] is not None:
            for token in re.findall(r"[A-Za-z][A-Za-z0-9]*-\d+", line):
                if ns.is_requirement_id(token) and token not in found[-1][1]:
                    found[-1][1].append(token)
    return [(title, tuple(reqs)) for title, reqs in found if title is not None]


@dataclass
class PostmanSource:
    path: Path | str  # a file, or an http(s) URL
    name: str = "postman"

    def items(self) -> list[SourceItem]:
        data = read_document(self.path)
        if not isinstance(data, dict) or "item" not in data:
            raise GapSourceError(f"{self.path}: not a Postman collection")
        out: list[SourceItem] = []

        def walk(nodes: Sequence[Mapping[str, Any]]) -> None:
            for node in nodes:
                if "item" in node:
                    walk(node["item"])
                    continue
                req = node.get("request") or {}
                url = req.get("url") or {}
                if isinstance(url, str):
                    path = "/" + url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
                else:
                    path = "/" + "/".join(str(p) for p in url.get("path") or ())
                method = str(req.get("method") or "GET")
                key = operation_key(method, normalise(path))
                if all(i.key != key for i in out):
                    out.append(SourceItem(self.name, key, str(node.get("name") or "")))

        walk(data["item"])
        if not out:
            raise GapSourceError(f"{self.path}: lists no request")
        return out


@dataclass
class RequirementSource:
    registry: Registry
    name: str = "requirement"

    def items(self) -> list[SourceItem]:
        out = [SourceItem(self.name, r.id, r.title, (r.id,)) for r in self.registry.active()]
        if not out:
            raise GapSourceError("the requirements registry lists no active requirement")
        return out


# ---------------------------------------------------------------------- detection


@dataclass(frozen=True)
class GapItem:
    item: SourceItem
    status: str
    covering_tests: tuple[str, ...] = ()
    exercised_by: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.item.source,
            "key": self.item.key,
            "title": self.item.title,
            "status": self.status,
            "covering_tests": list(self.covering_tests),
            "exercised_by": list(self.exercised_by),
        }


@dataclass
class GapReport:
    sources: list[dict[str, Any]]
    items: list[GapItem]
    runtime: bool = False
    new_gaps: list[str] = field(default_factory=list)
    closed_gaps: list[str] = field(default_factory=list)
    ratchet: dict[str, Any] | None = None  # {new, closed, passed} once a baseline was applied
    groups: list[dict[str, Any]] | None = None  # report only
    frozen: dict[str, Any] | None = None  # report only
    generated_at: str = field(default_factory=utc_now)

    @property
    def gaps(self) -> list[GapItem]:
        return [i for i in self.items if i.status == GAP]

    def counts(self) -> dict[str, int]:
        out = {COVERED: 0, COVERED_NOT_PASSING: 0, EXERCISED: 0, GAP: 0}
        for item in self.items:
            out[item.status] += 1
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "mode": "runtime" if self.runtime else "static",
            "sources": self.sources,
            "counts": self.counts(),
            "items": [i.to_json() for i in self.items],
            "new_gaps": self.new_gaps,
            "closed_gaps": self.closed_gaps,
            "ratchet": self.ratchet,
            "groups": self.groups,
            "frozen": self.frozen,
        }

    def to_markdown(self) -> str:
        counts = self.counts()
        lines = [
            "## Coverage gaps",
            "",
            f"Mode: {'runtime + static' if self.runtime else 'static'} · "
            + " · ".join(f"{k} {v}" for k, v in counts.items()),
            "",
        ]
        if self.new_gaps:
            lines += ["**New gaps (not in the baseline):**", ""]
            lines += [f"- `{g}`" for g in self.new_gaps] + [""]
        if self.closed_gaps:
            lines += ["Closed since the baseline (remove them from it):", ""]
            lines += [f"- `{g}`" for g in self.closed_gaps] + [""]
        if self.groups is not None:
            lines += ["### Gaps per group (report only)", "", "| Group | Items | Uncovered |"]
            lines += ["|---|---|---|"]
            lines += [f"| {g['group']} | {g['items']} | {g['uncovered']} |" for g in self.groups]
            lines.append("")
        if self.frozen is not None:
            f = self.frozen
            lines += [f"### Frozen ref `{f['ref']}` (report only)", ""]
            if not f.get("measured"):
                lines += [f"not measured: {f.get('reason')}", ""]
            else:
                lines.append(f"{f['gaps']} gap(s) at the ref, {len(self.gaps)} at HEAD")
                for label, key in (("only at HEAD", "only_head"), ("only at the ref", "only_ref")):
                    if f[key]:
                        lines += [f"- {label}: " + ", ".join(f"`{x}`" for x in f[key][:50])]
                lines.append("")
        for source in self.sources:
            name = source["name"]
            gaps = [i for i in self.gaps if i.item.source == name]
            lines.append(f"### {name}: {len(gaps)} gap(s) of {source['items']}")
            lines.append("")
            lines += [f"- `{i.item.key}` {i.item.title}".rstrip() for i in gaps[:200]]
            if len(gaps) > 200:
                lines.append(f"- ... and {len(gaps) - 200} more (see gaps.json)")
            lines.append("")
        if self.runtime:
            lines.append("_Exercised = called during the run; a call is not an assertion._")
        return "\n".join(lines) + "\n"


def static_evidence(items: Iterable[Mapping[str, Any]]) -> tuple[dict[str, set], dict[str, set]]:
    """``(covers -> nodeids, requirement -> nodeids)`` from collected items."""
    covers: dict[str, set[str]] = {}
    reqs: dict[str, set[str]] = {}
    for item in items:
        for tag in item.get("covers") or ():
            covers.setdefault(_cover_key(tag), set()).add(item["nodeid"])
        for req in item.get("requirements") or ():
            reqs.setdefault(req, set()).add(item["nodeid"])
    return covers, reqs


def _cover_key(tag: str) -> str:
    """``GET /a/{id}`` -> ``GET /a/{}``; ``spec:x#y`` unchanged."""
    text = tag.strip()
    method, _, path = text.partition(" ")
    if path.startswith("/") and method.isalpha():
        return operation_key(method, normalise(path))
    return text


def _item_cover_keys(item: SourceItem) -> list[str]:
    keys = [item.ref, item.key]
    method, _, path = item.key.partition(" ")
    if path.startswith("/"):
        keys.append(operation_key(method, normalise(path)))
    return keys


def detect_gaps(
    sources: Sequence[GapSource],
    links: Iterable[Mapping[str, Any]],
    hits: Iterable[EndpointHit] = (),
    results: Mapping[str, str] | None = None,
) -> GapReport:
    """Classify every source item.

    ``links`` are collected items (``nodeid``, ``requirements``, ``covers``). ``results``
    maps node id -> latest outcome; with it, an item whose covering tests did not pass is
    ``covered_not_passing``.
    """
    covers, reqs = static_evidence(links)
    hit_list = list(hits)
    source_items: list[tuple[GapSource, list[SourceItem]]] = []
    for source in sources:
        source_items.append((source, list(source.items())))
    operations = [
        (i.key.split(" ", 1)[0], i.key.split(" ", 1)[1])
        for _, items in source_items
        for i in items
        if " /" in i.key
    ]
    exercised: dict[str, set[str]] = {}
    for hit, key in iter_matched(hit_list, operations):
        method, template = key.split(" ", 1)
        exercised.setdefault(operation_key(method, normalise(template)), set()).add(
            hit.nodeid or "?"
        )
    out: list[GapItem] = []
    for _source, items in source_items:
        for item in items:
            nodes: set[str] = set()
            for key in _item_cover_keys(item):
                nodes |= covers.get(key, set())
            for req in item.requirements:
                nodes |= reqs.get(req, set())
            ex = set()
            for key in _item_cover_keys(item):
                ex |= exercised.get(key, set())
            if nodes:
                status = COVERED
                if results is not None and not any(results.get(n) == "passed" for n in nodes):
                    status = COVERED_NOT_PASSING
            elif ex:
                status = EXERCISED
            else:
                status = GAP
            out.append(GapItem(item, status, tuple(sorted(nodes)), tuple(sorted(ex))))
    return GapReport(
        sources=[{"name": s.name, "items": len(i)} for s, i in source_items],
        items=out,
        runtime=bool(hit_list),
    )


# ---------------------------------------------------------------------- ratchet


@dataclass
class RatchetResult:
    new_gaps: list[str]
    closed_gaps: list[str]
    strict: bool = True  # a closed gap still in the baseline fails until it is deleted

    @property
    def ok(self) -> bool:
        return not self.new_gaps and not (self.strict and self.closed_gaps)

    @property
    def passed(self) -> bool:
        return self.ok

    def messages(self) -> list[str]:
        out = [f"NEW GAP: {g}" for g in self.new_gaps]
        if self.strict:
            out += [f"stale baseline entry, delete it: {g}" for g in self.closed_gaps]
        elif self.closed_gaps:
            out.append(f"{len(self.closed_gaps)} gap(s) closed: remove them from the baseline")
        return out


def load_baseline(path: Path) -> set[str]:
    data = read_json(Path(path))
    if data is None:
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("accepted"), list):
        raise GapSourceError(f"{path}: a baseline is {{'accepted': [...]}}")
    return {str(x) for x in data["accepted"]}


def ratchet(report: GapReport, baseline: Path, *, strict: bool = True) -> RatchetResult:
    accepted = load_baseline(baseline)
    current = {g.item.ref for g in report.gaps}
    result = RatchetResult(sorted(current - accepted), sorted(accepted - current), strict)
    report.new_gaps, report.closed_gaps = result.new_gaps, result.closed_gaps
    report.ratchet = {"new": result.new_gaps, "closed": result.closed_gaps, "passed": result.ok}
    return result


# ---------------------------------------------------------------------- report-only views


def group_gaps(report: GapReport, groups: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    """Per group: how many source items it ships and how many are uncovered.

    A selector is a prefix of the item's ``source:key`` or key, or ``tag:<id>`` for an item
    that carries that requirement. An item in no group lands in ``(ungrouped)``.
    """

    def matches(item: SourceItem, selector: str) -> bool:
        if selector.startswith("tag:"):
            return selector[4:] in item.requirements
        return item.ref.startswith(selector) or item.key.startswith(selector)

    rows = []
    grouped: set[str] = set()
    for name, selectors in groups.items():
        members = [g for g in report.items if any(matches(g.item, s) for s in selectors)]
        grouped |= {g.item.ref for g in members}
        rows.append(
            {
                "group": str(name),
                "items": len(members),
                "uncovered": sum(1 for g in members if g.status == GAP),
            }
        )
    rest = [g for g in report.items if g.item.ref not in grouped]
    rows.append(
        {
            "group": "(ungrouped)",
            "items": len(rest),
            "uncovered": sum(1 for g in rest if g.status == GAP),
        }
    )
    return rows


def load_groups(path: Path) -> dict[str, list[str]]:
    from traceability._util import load_yaml

    data = load_yaml(Path(path)) if Path(path).suffix in (".yaml", ".yml") else read_json(path)
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        raise GapSourceError(f"{path}: groups are a mapping of name -> list of selectors")
    return {str(k): [str(s) for s in v] for k, v in data.items()}


def _git_show(root: Path, ref: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def _git_list(root: Path, ref: str, directory: str) -> list[str] | None:
    proc = subprocess.run(
        ["git", "ls-tree", "--name-only", f"{ref}:{directory}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.split() if proc.returncode == 0 else None


def frozen_view(
    report: GapReport,
    ref: str,
    *,
    root: Path,
    openapi: Sequence[str] = (),
    specs: str | None = None,
    postman: Sequence[str] = (),
    links: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The same file sources read at ``ref``: its gap count and the ids only one side has.

    A source file the ref does not have makes the view "not measured", never "no gaps".
    The suite's links are HEAD's: this measures what the frozen sources asked for against
    the tests as they are now. Registry-based items are not re-read.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        sources: list[GapSource] = []
        for kind, paths in (("openapi", openapi), ("postman", postman)):
            for rel in paths:
                rel = Path(rel).resolve().relative_to(root.resolve()).as_posix()
                blob = _git_show(root, ref, rel)
                if blob is None:
                    return {"ref": ref, "measured": False, "reason": f"{rel} is not at {ref}"}
                target = base / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
                sources.append(
                    OpenApiSource(target) if kind == "openapi" else PostmanSource(target)
                )
        if specs:
            rel = Path(specs).resolve().relative_to(root.resolve()).as_posix()
            names = _git_list(root, ref, rel)
            if names is None:
                return {"ref": ref, "measured": False, "reason": f"{rel}/ is not at {ref}"}
            (base / rel).mkdir(parents=True, exist_ok=True)
            for name in names:
                if name.endswith(".md"):
                    (base / rel / name).write_bytes(_git_show(root, ref, f"{rel}/{name}") or b"")
            sources.append(SpecSource(base / rel))
        if not sources:
            return {"ref": ref, "measured": False, "reason": "no file source to re-read"}
        try:
            frozen = detect_gaps(sources, links)
        except GapSourceError as exc:
            return {"ref": ref, "measured": False, "reason": str(exc)}
    names = {s.name for s in sources}
    head_ids = {i.item.ref for i in report.items if i.item.source in names}
    ref_ids = {i.item.ref for i in frozen.items}
    return {
        "ref": ref,
        "measured": True,
        "gaps": len(frozen.gaps),
        "only_head": sorted(head_ids - ref_ids),
        "only_ref": sorted(ref_ids - head_ids),
    }


def write_baseline(report: GapReport, path: Path) -> Path:
    from traceability._util import write_text

    body = {
        "description": "Accepted coverage gaps. Shrink it when a gap closes; grow it only in "
        "a reviewed pull request.",
        "accepted": sorted(g.item.ref for g in report.gaps),
    }
    return write_text(Path(path), json.dumps(body, indent=2) + "\n")


def report_json(report: GapReport) -> dict[str, Any]:
    data = report.to_json()
    validate(data, "gap-report.schema.json")
    return data
