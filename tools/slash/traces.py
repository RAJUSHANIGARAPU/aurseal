"""
A Playwright trace archive -> a pytest module (and optionally a page object), through
the repository's ``TestGenerator``.

    python -m tools.slash.traces list [--dir reports/traces]
    python -m tools.slash.traces generate [--trace T.zip] [--name flow] [--page-object]
                                         [--overwrite]

``generate`` resolves the trace (the newest in the folder when none is given), checks
it is a readable trace archive, refuses to run without generator credentials (and says
so - it never writes placeholder code), writes ``ui/tests/test_<name>.py`` and, when
asked or when none exists, ``ui/pages/<name>_page.py``. An existing target is not
replaced unless ``--overwrite`` is given (the command asks first). Every written file is
compiled; a compile error exits 2 before any test runs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

TRACE_DIR = Path("reports/traces")
UI_TESTS = Path("ui/tests")
PAGES = Path("ui/pages")
_SLEEP = re.compile(r"\btime\.sleep\(|\bwait_for_timeout\(|\basyncio\.sleep\(")


class TraceError(ValueError):
    pass


def newest(directory: Path = TRACE_DIR, count: int = 3) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:count]


def validate(path: Path) -> None:
    if not path.exists():
        raise TraceError(f"no trace at {path}")
    if path.suffix.lower() != ".zip" or not zipfile.is_zipfile(path):
        raise TraceError(f"{path} is not a zip archive, so not a Playwright trace")
    with zipfile.ZipFile(path) as archive:
        if not any(n.endswith(".trace") or n == "trace.json" for n in archive.namelist()):
            raise TraceError(f"{path} has no .trace member; it is not a Playwright trace")


def first_url(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".trace"):
                continue
            for line in archive.read(name).decode("utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                params = event.get("params") or {}
                url = params.get("url") or event.get("url") or ""
                if url.startswith("http"):
                    return url
    return ""


def name_from_url(url: str) -> str:
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s and not s.isdigit()]
    raw = segments[-1] if segments else (parts.hostname or "flow").split(".")[0]
    name = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return name if name and name[0].isalpha() else f"flow_{name or 'page'}"


def class_name(flow: str) -> str:
    return "".join(p.capitalize() for p in flow.split("_")) + "Page"


def url_pattern(url: str) -> str:
    parts = urlsplit(url)
    return re.sub(r"/\d+(?=/|$)", "/{id}", parts.path or "/")


@dataclass
class Outcome:
    written: list[Path] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    code: int = 0


def check_code(source: str, *, page_object: bool) -> list[str]:
    problems = []
    if _SLEEP.search(source):
        problems.append("contains a sleep")
    if page_object and not re.search(r"class \w+\(\s*(?:\w+\.)*BasePage\s*\)", source):
        problems.append("page object does not extend BasePage")
    return problems


def generate(
    trace: Path | None,
    name: str | None,
    *,
    page_object: bool = False,
    overwrite: bool = False,
    generator=None,
    trace_dir: Path = TRACE_DIR,
    tests_dir: Path = UI_TESTS,
    pages_dir: Path = PAGES,
) -> Outcome:
    out = Outcome()
    if trace is None:
        candidates = newest(trace_dir)
        if not candidates:
            out.messages.append(f"no trace in {trace_dir}; record one with --tracing=on")
            out.code = 1
            return out
        trace = candidates[0]
        out.messages.append("newest traces: " + ", ".join(str(p) for p in candidates))
    out.messages.append(f"trace: {trace}")
    try:
        validate(trace)
    except TraceError as err:
        out.messages.append(f"stop: {err}")
        out.code = 1
        return out
    url = first_url(trace)
    flow = name or name_from_url(url)
    if generator is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            out.messages.append(
                "stop: the generator is unavailable (ANTHROPIC_API_KEY is not set); nothing written"
            )
            out.code = 1
            return out
        from core.ai.test_generator import TestGenerator

        generator = TestGenerator()

    test_path = tests_dir / f"test_{flow}.py"
    page_path = pages_dir / f"{flow}_page.py"
    want_page = page_object or not page_path.exists()
    for path in [test_path] + ([page_path] if want_page else []):
        if path.exists() and not overwrite:
            out.messages.append(f"stop: {path} exists; confirm before overwriting (--overwrite)")
            out.code = 1
            return out

    code = generator.generate_from_trace(trace, page_name=flow)
    if not code:
        out.messages.append("stop: the generator returned nothing; nothing written")
        out.code = 1
        return out
    page_code = None
    if want_page:
        page_code = generator.generate_page_object(
            class_name(flow), url_pattern(url), f"The screen the recorded '{flow}' flow runs on."
        )
        if not page_code:
            out.messages.append("stop: no page object came back; nothing written")
            out.code = 1
            return out
    problems = check_code(code, page_object=False)
    if page_code:
        problems += check_code(page_code, page_object=True)
    if problems:
        out.messages.append("stop: generated code breaks the conventions: " + "; ".join(problems))
        out.code = 1
        return out

    files = [(test_path, code)] + ([(page_path, page_code)] if page_code else [])
    for path, source in files:
        try:
            compile(source, str(path), "exec")
        except SyntaxError as err:
            out.messages.append(f"stop: generated {path.name} does not compile: {err}")
            out.code = 2
            return out
    for path, source in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        out.written.append(path)
        out.messages.append(f"wrote {path}")
    out.messages.append(f"next: pytest {test_path} -v")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Playwright trace -> pytest test (+ page object).")
    sub = parser.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--dir", type=Path, default=TRACE_DIR)
    gen = sub.add_parser("generate")
    gen.add_argument("--trace", type=Path)
    gen.add_argument("--name")
    gen.add_argument("--page-object", action="store_true")
    gen.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "list":
        found = newest(args.dir)
        print("\n".join(str(p) for p in found) or f"no traces in {args.dir}")
        return 0 if found else 1
    outcome = generate(
        args.trace, args.name, page_object=args.page_object, overwrite=args.overwrite
    )
    print("\n".join(outcome.messages))
    return outcome.code


if __name__ == "__main__":
    sys.exit(main())
