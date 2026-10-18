"""Turn a recorded Playwright trace into a replayable seed fixture.

A tester clicks a journey through once with tracing on; this reads the trace and writes
the API calls that journey made as a :mod:`test_data.fixture_replay` fixture, so the
same data can be seeded over the API without a browser::

    python -m test_data.trace_to_fixture reports/traces/order.zip --out fixtures/order.json \\
        --path-pattern '^/api/' --date-field start_date --date-field effective_date

What it does, in order:

1. reads every network entry of the trace (a ``.zip`` or an unpacked trace directory):
   ``*.network`` lines of type ``resource-snapshot``;
2. keeps the writes (``--method``, default POST and PUT) whose URL path matches
   ``--path-pattern``; request and response bodies are resolved from ``resources/<sha1>``
   when the trace stores them out of line;
3. strips dynamic values: the entity version in the path (``--version-segment``, default
   ``/versions/<n>``), a ``version`` query parameter (both become ``${version}``), and
   the named date fields in request bodies (become ``${TODAY_TOKEN}``);
4. replaces ids the trace created with placeholders: a create (a successful POST whose
   response carries an ``id`` that is not already in its path) captures
   ``${<collection>_id}``, and every later use of that id in a path segment or under an
   id-like body key (``id``, ``customer_id``) refers to it;
5. drops abandoned entities: a create whose id nothing later uses, unless it is the last
   create (the journey's subject);
6. reports the step count, the terminus (the last kept call and the status it left the
   entity in) and whether coverage is partial: a kept call that failed (non-2xx) or a body
   that could not be resolved from the trace.

Nothing is sent anywhere; the input trace is not modified.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

DEFAULT_METHODS = ("POST", "PUT")
DEFAULT_PATH_PATTERN = r"^/api/"
DEFAULT_VERSION_SEGMENT = r"/versions/\d+"
VERSION_PLACEHOLDER = "${version}"
DATE_PLACEHOLDER = "${TODAY_TOKEN}"


@dataclass
class Call:
    method: str
    path: str
    query: list[tuple[str, str]]
    body: Any
    status: int | None
    response: Any
    unresolved: list[str] = field(default_factory=list)


@dataclass
class ExtractResult:
    fixture: dict[str, Any]
    step_count: int
    terminus: str
    partial: bool
    warnings: list[str]
    abandoned: list[str]

    def summary(self) -> str:
        lines = [
            f"steps: {self.step_count}",
            f"terminus: {self.terminus}",
            f"coverage: {'PARTIAL' if self.partial else 'complete'}",
        ]
        lines += [f"dropped abandoned entity: {a}" for a in self.abandoned]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


class _Trace:
    """Uniform read access to a trace zip or an unpacked trace directory."""

    def __init__(self, source: str | Path) -> None:
        self.source = Path(source)
        self._zip = zipfile.ZipFile(self.source) if self.source.is_file() else None

    def names(self) -> list[str]:
        if self._zip is not None:
            return self._zip.namelist()
        return [
            p.relative_to(self.source).as_posix() for p in self.source.rglob("*") if p.is_file()
        ]

    def read(self, name: str) -> bytes | None:
        try:
            if self._zip is not None:
                return self._zip.read(name)
            return (self.source / name).read_bytes()
        except (KeyError, OSError):
            return None

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


def _decode(raw: bytes | None, mime: str = "") -> Any:
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    if "json" in mime or text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def read_calls(trace: str | Path) -> list[Call]:
    """Every network call in the trace, in recorded order."""
    t = _Trace(trace)
    try:
        calls = []
        for name in sorted(n for n in t.names() if n.endswith(".network")):
            for line in (t.read(name) or b"").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") != "resource-snapshot":
                    continue
                snap = event.get("snapshot") or {}
                req, resp = snap.get("request") or {}, snap.get("response") or {}
                url = urlsplit(req.get("url", ""))
                unresolved: list[str] = []
                post = req.get("postData") or {}
                body: Any = None
                if "text" in post:
                    body = _decode(post["text"].encode(), post.get("mimeType", ""))
                elif post.get("_sha1"):
                    raw = t.read(f"resources/{post['_sha1']}")
                    if raw is None:
                        unresolved.append(f"request body resources/{post['_sha1']}")
                    body = _decode(raw, post.get("mimeType", ""))
                content = resp.get("content") or {}
                response: Any = None
                if "text" in content:
                    response = _decode(content["text"].encode(), content.get("mimeType", ""))
                elif content.get("_sha1"):
                    raw = t.read(f"resources/{content['_sha1']}")
                    if raw is None:
                        unresolved.append(f"response body resources/{content['_sha1']}")
                    response = _decode(raw, content.get("mimeType", ""))
                calls.append(
                    Call(
                        method=str(req.get("method", "GET")).upper(),
                        path=url.path,
                        query=parse_qsl(url.query, keep_blank_values=True),
                        body=body,
                        status=resp.get("status"),
                        response=response,
                        unresolved=unresolved,
                    )
                )
        return calls
    finally:
        t.close()


def _is_id_key(key: Any) -> bool:
    return isinstance(key, str) and key.lower().endswith("id")


def _replace_literal(value: Any, literal: str, placeholder: str, in_id: bool = False) -> Any:
    """Replace ``literal`` where it sits under an id-like key (``id``, ``customer_id``, ...).

    Only id-like keys are touched so an amount or a count that happens to equal an id
    keeps its value.
    """
    if isinstance(value, dict):
        return {
            k: _replace_literal(v, literal, placeholder, _is_id_key(k)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_replace_literal(v, literal, placeholder, in_id) for v in value]
    if in_id and not isinstance(value, bool) and isinstance(value, int | str):
        return placeholder if str(value) == literal else value
    return value


def _mentions(value: Any, literal: str, in_id: bool = False) -> bool:
    if isinstance(value, dict):
        return any(_mentions(v, literal, _is_id_key(k)) for k, v in value.items())
    if isinstance(value, list):
        return any(_mentions(v, literal, in_id) for v in value)
    return (
        in_id
        and not isinstance(value, bool)
        and isinstance(value, int | str)
        and str(value) == literal
    )


def _path_has(path: str, literal: str) -> bool:
    return literal in path.split("/")


def _strip_dates(body: Any, fields: set[str]) -> Any:
    if isinstance(body, dict):
        return {
            k: (
                DATE_PLACEHOLDER if k in fields and v not in (None, "") else _strip_dates(v, fields)
            )
            for k, v in body.items()
        }
    if isinstance(body, list):
        return [_strip_dates(v, fields) for v in body]
    return body


def _collection(path: str) -> str:
    segments = [s for s in path.split("/") if s and not s.isdigit()]
    name = re.sub(r"[^A-Za-z0-9]+", "_", segments[-1] if segments else "entity").strip("_")
    return name or "entity"


def extract(
    trace: str | Path,
    *,
    name: str = "",
    methods: Iterable[str] = DEFAULT_METHODS,
    path_pattern: str = DEFAULT_PATH_PATTERN,
    version_segment: str = DEFAULT_VERSION_SEGMENT,
    date_fields: Iterable[str] = (),
    status_field: str = "status",
) -> ExtractResult:
    """Read ``trace`` and build a fixture (see the module docstring for the rules)."""
    wanted = {m.upper() for m in methods}
    pattern = re.compile(path_pattern)
    version_re = re.compile(version_segment) if version_segment else None
    dates = set(date_fields)
    calls = [c for c in read_calls(trace) if c.method in wanted and pattern.search(c.path)]
    warnings: list[str] = []

    created: list[tuple[int, str]] = []  # (call index, id literal)
    for i, call in enumerate(calls):
        new_id = call.response.get("id") if isinstance(call.response, dict) else None
        if (
            call.method == "POST"
            and new_id not in (None, "")
            and call.status is not None
            and 200 <= int(call.status) < 300
            and not _path_has(call.path, str(new_id))
        ):
            created.append((i, str(new_id)))

    abandoned: set[int] = set()
    for pos, (i, literal) in enumerate(created):
        if pos == len(created) - 1:
            continue
        later = calls[i + 1 :]
        if not any(_path_has(c.path, literal) or _mentions(c.body, literal) for c in later):
            abandoned.add(i)
    dropped_ids = {lit for i, lit in created if i in abandoned}
    kept = [
        (i, c)
        for i, c in enumerate(calls)
        if i not in abandoned and not any(_path_has(c.path, lit) for lit in dropped_ids)
    ]

    placeholders: dict[str, str] = {}
    used_names: set[str] = set()
    steps: list[dict[str, Any]] = []
    partial = False
    for i, call in kept:
        path, body = call.path, _strip_dates(call.body, dates)
        if version_re is not None:
            path = version_re.sub(lambda m: re.sub(r"\d+", VERSION_PLACEHOLDER, m.group(0)), path)
        for literal, placeholder in placeholders.items():
            path = "/".join(placeholder if s == literal else s for s in path.split("/"))
            body = _replace_literal(body, literal, placeholder)
        query = [(k, VERSION_PLACEHOLDER if k == "version" else v) for k, v in call.query]
        step: dict[str, Any] = {"method": call.method, "path": path}
        if query:
            step["path"] += "?" + urlencode(query, safe="${}")
        if body is not None:
            step["body"] = body
        literal = next((lit for j, lit in created if j == i), None)
        if literal is not None:
            base = f"{_collection(call.path)}_id"
            var = base
            n = 2
            while var in used_names:
                var, n = f"{base}{n}", n + 1
            used_names.add(var)
            placeholders[literal] = f"${{{var}}}"
            step["capture"] = {var: "id"}
        if call.status is None or not 200 <= int(call.status) < 300:
            partial = True
            warnings.append(f"{call.method} {call.path} answered {call.status}")
        for missing in call.unresolved:
            partial = True
            warnings.append(f"{call.method} {call.path}: {missing} is not in the trace")
        steps.append(step)

    if not steps:
        partial = True
        warnings.append(f"no {sorted(wanted)} call matched {path_pattern!r}")
        terminus = "-"
    else:
        last = kept[-1][1]
        state = last.response.get(status_field) if isinstance(last.response, dict) else None
        terminus = f"{last.method} {steps[-1]['path']}" + (f" -> {state}" if state else "")

    fixture: dict[str, Any] = {"name": name or Path(trace).stem}
    if steps and "capture" in steps[0]:
        fixture["create"] = steps[0]
        fixture["steps"] = steps[1:]
    else:
        fixture["steps"] = steps
    return ExtractResult(
        fixture=fixture,
        step_count=len(steps),
        terminus=terminus,
        partial=partial,
        warnings=warnings,
        abandoned=[
            f"{calls[i].method} {calls[i].path} (id {lit})" for i, lit in created if i in abandoned
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m test_data.trace_to_fixture")
    parser.add_argument("trace", help="Playwright trace .zip or unpacked trace directory")
    parser.add_argument("--out", required=True, help="fixture JSON to write")
    parser.add_argument("--name", default="")
    parser.add_argument("--method", action="append", dest="methods")
    parser.add_argument("--path-pattern", default=DEFAULT_PATH_PATTERN)
    parser.add_argument("--version-segment", default=DEFAULT_VERSION_SEGMENT)
    parser.add_argument("--date-field", action="append", dest="date_fields", default=[])
    parser.add_argument("--status-field", default="status")
    args = parser.parse_args(argv)
    if not Path(args.trace).exists():
        print(f"error: {args.trace} does not exist")
        return 2
    result = extract(
        args.trace,
        name=args.name,
        methods=args.methods or DEFAULT_METHODS,
        path_pattern=args.path_pattern,
        version_segment=args.version_segment,
        date_fields=args.date_fields,
        status_field=args.status_field,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.fixture, indent=2) + "\n", encoding="utf-8")
    print(f"fixture written: {out}")
    print(result.summary())
    return 1 if result.step_count == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
