"""
What is specified but not tested: scenario specs, OpenAPI documents and Postman
collections against the UI and API test modules.

Rules:

* a scenario is covered when some collected test function name contains
  ``test_`` + the scenario name in snake_case (lowercase, every run of
  non-alphanumerics turned into ``_``, trimmed);
* an operation is covered when an API test calls its path with the same method
  (``client.get("/users/42")`` covers ``GET /users/{id}``), or a test declares it
  with ``@pytest.mark.covers("GET /users/{id}")``;
* the folder's ``README.md`` (the format guide) is not a spec.

This is the readable report for people. The CI ratchet over explicit ``covers``
links is ``python -m traceability gaps``.

    python -m tools.slash.coverage_gaps [all | specs/x.md | https://host/openapi.json]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
SPEC_DIR = Path("specs")
UI_TESTS = (Path("ui/tests"),)
API_TESTS = (Path("api/tests"),)
OPENAPI_ROOTS = (Path("specs"), Path("api"), Path("demo_sut"), Path("schemas"))
MAX_DOC_BYTES = 5 * 1024 * 1024

_SCENARIO = re.compile(r"^#{2,4}\s+Scenario\b\s*:?\s*(.+?)\s*$", re.IGNORECASE)
_TAGS = re.compile(r"^#{2,4}\s+Tags\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,4}\s")
_FEATURE = re.compile(r"^feature\s*:\s*(.+?)\s*$", re.IGNORECASE)


def snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def test_name_for(scenario: str) -> str:
    return f"test_{snake(scenario)}"


# ------------------------------------------------------------------ sources


@dataclass
class Scenario:
    name: str
    tags: list[str] = field(default_factory=list)


@dataclass
class SpecFile:
    path: Path
    feature: str
    scenarios: list[Scenario]


def is_format_guide(path: Path) -> bool:
    return path.stem.lower() in {"readme", "format", "format-guide", "spec-format"}


def parse_spec(path: Path) -> SpecFile:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    feature = path.stem
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = _FEATURE.match(line.strip())
            if match:
                feature = match.group(1).strip("'\"")
    scenarios: list[Scenario] = []
    in_tags = False
    for line in lines:
        match = _SCENARIO.match(line)
        if match:
            scenarios.append(Scenario(match.group(1)))
            in_tags = False
        elif _TAGS.match(line):
            in_tags = True
        elif _HEADING.match(line):
            in_tags = False
        elif in_tags and scenarios and line.strip():
            scenarios[-1].tags += re.findall(r"`([^`]+)`", line) or line.split()
    return SpecFile(path, feature, scenarios)


def spec_files(directory: Path = SPEC_DIR) -> list[Path]:
    return [p for p in sorted(directory.glob("*.md")) if not is_format_guide(p)]


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    tags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def openapi_operations(doc: dict) -> list[Operation]:
    ops = []
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                ops.append(Operation(method.upper(), path, tuple(op.get("tags") or ())))
    return ops


def postman_operations(doc: dict) -> list[Operation]:
    ops: list[Operation] = []

    def walk(items, folder):
        for item in items or []:
            if "item" in item:
                walk(item["item"], item.get("name", folder))
                continue
            request = item.get("request") or {}
            url = request.get("url") or ""
            if isinstance(url, dict):
                path = "/" + "/".join(str(p) for p in url.get("path") or [])
            else:
                raw = re.sub(r"\{\{[^}]+\}\}", "", str(url))
                path = urlsplit(raw if "://" in raw else "http://h/" + raw.lstrip("/")).path
            path = re.sub(r":(\w+)", r"{\1}", path) or "/"
            ops.append(Operation(str(request.get("method", "GET")).upper(), path, (folder,)))

    walk(doc.get("item"), doc.get("info", {}).get("name", ""))
    return ops


def kind_of(doc: object) -> str | None:
    if not isinstance(doc, dict):
        return None
    if "openapi" in doc or "swagger" in doc:
        return "openapi"
    if "getpostman.com" in str((doc.get("info") or {}).get("schema", "")):
        return "postman"
    return None


def api_documents(roots=OPENAPI_ROOTS) -> list[tuple[Path, str, dict]]:
    found = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.json")) if Path(root).exists() else []:
            if path.stat().st_size > MAX_DOC_BYTES or "__pycache__" in path.parts:
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            kind = kind_of(doc)
            if kind:
                found.append((path, kind, doc))
    return found


# -------------------------------------------------------------------- tests


@dataclass
class TestInventory:
    names: set[str] = field(default_factory=set)  # lowercase test function names
    calls: set[tuple[str, str]] = field(default_factory=set)  # (METHOD, path string)
    covers: set[str] = field(default_factory=set)  # "GET /path" declared by marker

    @property
    def has_tests(self) -> bool:
        return bool(self.names)


def _string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return None


def inventory(ui_dirs=UI_TESTS, api_dirs=API_TESTS) -> TestInventory:
    inv = TestInventory()
    for directory, is_api in [(d, False) for d in ui_dirs] + [(d, True) for d in api_dirs]:
        for path in sorted(Path(directory).rglob("test_*.py")) if Path(directory).exists() else []:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test"):
                    inv.names.add(node.name.lower())
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "covers":
                    inv.covers.update(s for a in node.args if (s := _string(a)))
                elif is_api and name.lower() in HTTP_METHODS and node.args:
                    if (s := _string(node.args[0])) is not None:
                        inv.calls.add((name.upper(), s))
                elif is_api and name == "request" and len(node.args) >= 2:
                    method, target = _string(node.args[0]), _string(node.args[1])
                    if method and target is not None:
                        inv.calls.add((method.upper(), target))
    return inv


def _template_regex(path: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\})", path.rstrip("/") or "/")
    body = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(rf"(?:^|[^/]){body}/?(?:\?.*)?$|^{body}/?(?:\?.*)?$")


def operation_covered(op: Operation, inv: TestInventory) -> bool:
    if op.key in inv.covers:
        return True
    rx = _template_regex(op.path)
    for method, target in inv.calls:
        if method != op.method:
            continue
        # a referenced "{}" placeholder from an f-string matches any one segment
        candidate = target.replace("{}", "x")
        path = urlsplit(candidate).path if "://" in candidate else candidate
        if rx.search(path):
            return True
    return False


def scenario_covered(scenario: Scenario, inv: TestInventory) -> bool:
    wanted = test_name_for(scenario.name)
    return any(wanted in name for name in inv.names)


# ------------------------------------------------------------------- report


@dataclass
class SourceResult:
    source: str
    kind: str  # spec | openapi | postman
    items: list[str]
    missing: list[str]

    @property
    def covered(self) -> int:
        return len(self.items) - len(self.missing)

    @property
    def status(self) -> str:
        if not self.items or not self.missing:
            return "full"
        return "none" if self.covered == 0 else "partial"


def check_spec(spec: SpecFile, inv: TestInventory) -> SourceResult:
    names = [s.name for s in spec.scenarios]
    missing = [s.name for s in spec.scenarios if not scenario_covered(s, inv)]
    return SourceResult(str(spec.path), "spec", names, missing)


def check_api(source: str, kind: str, ops: list[Operation], inv: TestInventory) -> SourceResult:
    keys = [op.key for op in ops]
    return SourceResult(
        source, kind, keys, [op.key for op in ops if not operation_covered(op, inv)]
    )


def render(results: list[SourceResult], inv: TestInventory, today: date) -> str:
    out = [f"# Coverage gaps - {today.isoformat()}", "", "## Summary", ""]
    out += ["| Source | Kind | Items | Tests exist | Covered | Gap |", "|---|---|---|---|---|---|"]
    for r in results:
        exists = "yes" if r.covered else ("no" if not inv.has_tests else "none matching")
        out.append(
            f"| `{r.source}` | {r.kind} | {len(r.items)} | {exists} | "
            f"{r.covered}/{len(r.items)} | {len(r.missing)} |"
        )
    sections = (
        ("No tests at all (highest priority)", "none"),
        ("Partially covered", "partial"),
        ("Fully covered", "full"),
    )
    for title, status in sections:
        out += ["", f"## {title}", ""]
        chosen = [r for r in results if r.status == status]
        if not chosen:
            out.append("(none)")
        for r in chosen:
            out.append(f"### `{r.source}` ({r.covered}/{len(r.items)})")
            if status != "full":
                out += [f"- missing: {item}" for item in r.missing]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def collect(target: str = "all", inv: TestInventory | None = None) -> list[SourceResult]:
    inv = inv or inventory()
    results: list[SourceResult] = []
    if target.startswith(("http://", "https://")):
        with urllib.request.urlopen(target, timeout=15) as response:  # noqa: S310 - user-given URL
            doc = json.loads(response.read(MAX_DOC_BYTES))
        return [check_api(target, "openapi", openapi_operations(doc), inv)]
    if target != "all":
        path = Path(target)
        if path.suffix == ".md":
            return [check_spec(parse_spec(path), inv)]
        doc = json.loads(path.read_text(encoding="utf-8"))
        kind = kind_of(doc) or "openapi"
        ops = openapi_operations(doc) if kind == "openapi" else postman_operations(doc)
        return [check_api(str(path), kind, ops, inv)]
    for spec in spec_files():
        results.append(check_spec(parse_spec(spec), inv))
    for path, kind, doc in api_documents():
        ops = openapi_operations(doc) if kind == "openapi" else postman_operations(doc)
        results.append(check_api(str(path), kind, ops, inv))
    return results


def save(report: str, out_dir: Path, today: date) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"coverage-gaps-{today.isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Specified but not tested.")
    parser.add_argument("target", nargs="?", default="all")
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    inv = inventory()
    results = collect(args.target, inv)
    today = date.today()
    report = render(results, inv, today)
    print(report)
    print(f"saved: {save(report, args.out, today)}")
    gaps = sum(len(r.missing) for r in results)
    print(f"gaps: {gaps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
