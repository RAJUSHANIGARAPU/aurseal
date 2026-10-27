"""Where the config preflight reads from: config at a git ref, paged HTTP lists, event history.

Everything here reads. Config, manifests and migration scripts come from
``git show REF:path`` (no checkout) or, offline, from ``DIR/config/<ref>/<path>``.
HTTP goes through a :class:`~gate.read_only.ReadOnlyClient`, live (a requests
session) or offline (:class:`FixtureHttp`, every GET answered from a file).

The HTTP shape assumed, all paths are templates with ``{type}`` (and ``{id}``):

* a list page: ``GET <path>?pageSize=N[&nextToken=T]`` ->
  ``{"items": [...], "nextToken": "T" | null}``; read until no token;
* one record: ``GET <record path>`` -> the record, 404 when it is gone;
* event history: the list shape over ``from``/``to`` (UTC instants), one
  item per "action entered" event: ``{"recordId", "state", "action"}``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import threading
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import yaml

from gate.read_only import ReadOnlyClient
from tools.config_preflight.config_preflight import (
    Config,
    InputError,
    Preflight,
    entity_from_mapping,
)

PAGE_SIZE_PARAM = "pageSize"
TOKEN_PARAM = "nextToken"
ITEMS_KEY = "items"
NEXT_KEY = "nextToken"
EVENT_RECORD_KEY = "recordId"
EVENT_STATE_KEY = "state"
EVENT_ACTION_KEY = "action"
EVENT_PAGE_SIZE = 500
REQUEST_TIMEOUT_S = 30

DEFAULT_PATHS = {
    "records": "/api/{type}/records",
    "drafts": "/api/{type}/drafts",
    "record": "/api/{type}/records/{id}",
    "events": "/api/{type}/events",
}


class SourceError(Exception):
    """A read that did not complete: the run continues, the result is undetermined."""


class NotAuthorised(SourceError):
    """403."""


class Gone(SourceError):
    """404 on a single record: it no longer exists."""


class RequestTimedOut(SourceError):
    pass


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def with_query(path: str, **params: Any) -> str:
    """``path`` with ``params`` merged into its query string (``None`` values dropped)."""
    parts = urlsplit(path)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items() if v is not None})
    base = path.split("?", 1)[0]
    return f"{base}?{urlencode(sorted(query.items()))}" if query else base


def get_json(client: ReadOnlyClient, path: str, *, retries: int = 0) -> Any:
    """GET and decode; a timed-out request is retried ``retries`` times."""
    for attempt in range(retries + 1):
        try:
            response = client.get(path, timeout=REQUEST_TIMEOUT_S)
        except Exception as exc:
            if _is_timeout(exc):
                if attempt < retries:
                    continue
                raise RequestTimedOut(f"GET {path}: timed out") from exc
            raise SourceError(f"GET {path}: {exc}") from exc
        status = getattr(response, "status_code", 200)
        if status == 403:
            raise NotAuthorised(f"GET {path}: not authorised (403)")
        if status == 404:
            raise Gone(f"GET {path}: 404")
        if status != 200:
            raise SourceError(f"GET {path}: HTTP {status}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(f"GET {path}: body is not JSON") from exc
    raise AssertionError("unreachable")


def read_pages(
    client: ReadOnlyClient,
    path: str,
    *,
    page_size: int,
    retries: int = 0,
    max_pages: int = 100_000,
    params: Mapping[str, Any] | None = None,
) -> Iterator[Mapping[str, Any]]:
    """Every item of a next-token paged list, to the end; a broken page raises."""
    token: str | None = None
    for _ in range(max_pages):
        url = with_query(path, **(params or {}), **{PAGE_SIZE_PARAM: page_size, TOKEN_PARAM: token})
        body = get_json(client, url, retries=retries)
        if not isinstance(body, Mapping) or not isinstance(body.get(ITEMS_KEY), list):
            raise SourceError(f"GET {url}: page has no {ITEMS_KEY!r} list")
        for item in body[ITEMS_KEY]:
            if not isinstance(item, Mapping):
                raise SourceError(f"GET {url}: an item is not an object")
            yield item
        token = body.get(NEXT_KEY) or None
        if token is None:
            return
    raise SourceError(f"GET {path}: more than {max_pages} pages")


@dataclass(frozen=True)
class HttpRecordReader:
    """A :class:`~tools.config_preflight.config_preflight.RecordReader` over a paged list."""

    client: ReadOnlyClient
    path: str
    page_size: int = 200

    def records(self) -> Iterator[Mapping[str, Any]]:
        yield from read_pages(self.client, self.path, page_size=self.page_size)


def fetch_record(client: ReadOnlyClient, path: str) -> Mapping[str, Any]:
    body = get_json(client, path, retries=1)
    if not isinstance(body, Mapping):
        raise SourceError(f"GET {path}: the record is not an object")
    return body


def _fixture_key(url: str) -> str:
    parts = urlsplit(url)
    query = sorted(parse_qsl(parts.query, keep_blank_values=True))
    return parts.path + (f"?{urlencode(query)}" if query else "")


class _FixtureResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


class FixtureHttp:
    """Answers GETs from ``DIR/http/*.json``; an unanswered GET is an error, not an empty page.

    Each file holds one object or a list of them::

        {"get": "/api/subscription/records?pageSize=200", "status": 200, "body": {...}}
        {"get": "/api/subscription/events?...", "sequence": [{"status": "timeout"}, {...}]}

    The key is the path plus the query, parameters in any order. A ``sequence``
    answers its entries in turn and then repeats the last; ``"status": "timeout"``
    raises :class:`TimeoutError`. ``calls`` records every GET, so a test can
    assert what was (not) read.
    """

    def __init__(self, directory: str | Path) -> None:
        self._answers: dict[str, list[Mapping[str, Any]]] = {}
        self._served: dict[str, int] = {}
        self._lock = threading.Lock()
        self.calls: list[str] = []
        http = Path(directory) / "http"
        for file in sorted(http.glob("*.json")) if http.is_dir() else []:
            data = json.loads(file.read_text(encoding="utf-8"))
            for entry in data if isinstance(data, list) else [data]:
                key = _fixture_key(str(entry["get"]))
                self._answers[key] = list(entry.get("sequence") or [entry])

    def get(self, url: str, **_: Any) -> _FixtureResponse:
        key = _fixture_key(url)
        with self._lock:
            self.calls.append(key)
            answers = self._answers.get(key)
            if answers is None:
                raise LookupError(f"no fixture for GET {key}")
            served = self._served.get(key, 0)
            self._served[key] = served + 1
        answer = answers[min(served, len(answers) - 1)]
        if answer.get("status") == "timeout":
            raise TimeoutError(f"fixture timeout for GET {key}")
        return _FixtureResponse(int(answer.get("status", 200)), answer.get("body"))


def read_token(env_name: str | None, file: str | None) -> str | None:
    """The bearer token from a file (first) or an environment variable; never from argv."""
    if file:
        try:
            token = Path(file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise InputError(f"cannot read token file {file}: {exc}") from exc
        if not token:
            raise InputError(f"token file {file} is empty")
        return token
    return os.environ.get(env_name) if env_name else None


def live_client(base_url: str, token: str | None) -> ReadOnlyClient:
    import requests

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return ReadOnlyClient.over(requests.Session(), base_url, headers=headers)


# ---------------------------------------------------------------------------
# config, manifest and migration scripts at a ref
# ---------------------------------------------------------------------------


class GitSource:
    """``git show REF:path`` in ``repo``; nothing is checked out."""

    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo)
        self._verified: set[str] = set()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=False
            )
        except OSError as exc:
            raise InputError(f"cannot run git: {exc}") from exc

    def read(self, ref: str, path: str) -> str | None:
        """The file at ``ref``, or ``None`` when that ref has no such file."""
        if ref not in self._verified:
            if self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode:
                raise InputError(f"unknown git ref {ref!r} in {self.repo}")
            self._verified.add(ref)
        proc = self._git("show", f"{ref}:{path}")
        return proc.stdout if proc.returncode == 0 else None


class FixtureConfigSource:
    """``DIR/config/<ref>/<path>``: the offline stand-in for :class:`GitSource`."""

    def __init__(self, directory: str | Path) -> None:
        self.root = Path(directory) / "config"

    def read(self, ref: str, path: str) -> str | None:
        base = self.root / ref
        if not base.is_dir():
            raise InputError(f"unknown ref {ref!r}: no {base}")
        file = base / path
        return file.read_text(encoding="utf-8") if file.is_file() else None


def load_typed_config(
    source: GitSource | FixtureConfigSource,
    ref: str,
    types: Iterable[str],
    templates: Iterable[str],
) -> tuple[Config, list[str]]:
    """Each type's config at ``ref``, from the first template that exists.

    A type no template finds is returned in the second list (COULD-NOT-DETERMINE),
    never silently dropped.
    """
    templates = list(templates)
    config: Config = {}
    missing: list[str] = []
    for kind in types:
        spellings = dict.fromkeys((kind, kind.lower()))
        tried = list(dict.fromkeys(t.format(type=k) for t in templates for k in spellings))
        for path in tried:
            text = source.read(ref, path)
            if text is None:
                continue
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise InputError(f"{ref}:{path} is not valid YAML: {exc}") from exc
            entity = entity_from_mapping(raw, f"{ref}:{path}")
            config[entity.key] = entity
            break
        else:
            missing.append(f"{kind}: could not determine, no config at {ref} (tried {tried})")
    return config, missing


def parse_ref_spec(spec: str) -> tuple[str, str]:
    """``PATH@REF`` -> (path, ref)."""
    path, sep, ref = spec.rpartition("@")
    if not sep or not path or not ref:
        raise InputError(f"expected PATH@REF, got {spec!r}")
    return path, ref


def read_manifest(
    source: GitSource | FixtureConfigSource, spec: str, target: str | None
) -> list[str]:
    """The entity types a manifest lists for ``target`` (matched case-insensitively).

    Either ``types: [...]`` or ``targets: {<target>: [...]}``.
    """
    path, ref = parse_ref_spec(spec)
    text = source.read(ref, path)
    if text is None:
        raise InputError(f"no manifest at {ref}:{path}")
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, Mapping):
        raise InputError(f"{ref}:{path}: a manifest must be a mapping")
    if target is None:
        if "types" not in raw:
            raise InputError(f"{ref}:{path} lists targets; pass --target")
        types = raw["types"]
    else:
        targets = raw.get("targets") or {}
        match = [v for k, v in targets.items() if str(k).casefold() == target.casefold()]
        if not match:
            raise InputError(f"{ref}:{path}: no target {target!r} (has {sorted(targets)})")
        types = match[0]
    if not isinstance(types, list) or not types:
        raise InputError(f"{ref}:{path}: the type list is empty")
    return [str(t) for t in types]


def read_migration_scripts(
    source: GitSource | FixtureConfigSource,
    ref: str,
    types: Iterable[str],
    templates: Iterable[str],
) -> dict[str, str]:
    """Each type's migration script at ``ref`` (first template that exists); absent = no hint."""
    templates = list(templates)
    found: dict[str, str] = {}
    for kind in types:
        for template in templates:
            text = source.read(ref, template.format(type=kind))
            if text is not None:
                found[kind] = text
                break
    return found


# ---------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------


def day_windows(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        raise InputError(f"--to {end} is before --from {start}")
    return [start + dt.timedelta(days=n) for n in range((end - start).days + 1)]


def _scan_day(
    client: ReadOnlyClient, path: str, day: dt.date
) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    window = {
        "from": f"{day.isoformat()}T00:00:00Z",
        "to": f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00Z",
    }
    events: list[Mapping[str, Any]] = []
    error = None
    try:
        for event in read_pages(client, path, page_size=EVENT_PAGE_SIZE, retries=1, params=window):
            events.append(event)
    except NotAuthorised:
        error = "not authorised (403)"
    except SourceError as exc:
        error = str(exc)
    return events, {"day": day.isoformat(), "count": len(events), "error": error}


def scan_events(
    client: ReadOnlyClient, path: str, days: list[dt.date], workers: int = 4
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    """Every day's events, several days at a time; a failed day is recorded, the rest go on."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(lambda d: _scan_day(client, path, d), days))
    events = [e for day_events, _ in outcomes for e in day_events]
    return events, [row for _, row in outcomes]


@dataclass
class ScanPlan:
    types: list[str]
    paths: Mapping[str, str]
    lists: tuple[str, ...] = ("records", "drafts")
    page_size: int = 200
    days: list[dt.date] | None = None  # event history window, None = off
    workers: int = 4
    ids: list[tuple[str | None, str]] | None = None  # (type or None, id)


def _fetch_and_check(
    pre: Preflight, client: ReadOnlyClient, plan: ScanPlan, kind: str, rid: str, source: str
) -> None:
    path = plan.paths["record"].format(type=kind, id=rid)
    try:
        record = fetch_record(client, path)
    except Gone:
        pre.result.gone.append({"entity": kind, "record": rid, "source": source})
        return
    except SourceError as exc:
        pre.result.undetermined.append(f"{kind} {source} {rid}: {exc}")
        return
    pre.check(record, source, entity=kind)


def scan(pre: Preflight, client: ReadOnlyClient, plan: ScanPlan) -> None:
    """Read every source for every type in ``plan`` and judge what comes back."""
    result = pre.result
    id_key = pre.shape.id_key
    for kind in plan.types:
        seen: set[str] = set()
        counted = result.scanned.setdefault(kind, {})
        for source in plan.lists:
            counted.setdefault(source, 0)  # a list read to the end with nothing in it shows 0
            path = plan.paths[source].format(type=kind)
            try:
                for record in read_pages(client, path, page_size=plan.page_size):
                    seen.add(str(record.get(id_key)))
                    pre.check(record, source, entity=kind)
            except SourceError as exc:  # NotAuthorised included: reported, the run goes on
                result.undetermined.append(f"{kind} {source}: {exc}")
        if plan.days is not None:
            counted.setdefault("events", 0)
            path = plan.paths["events"].format(type=kind)
            events, rows = scan_events(client, path, plan.days, plan.workers)
            result.days.extend({"entity": kind, **row} for row in rows)
            failed = [r for r in rows if r["error"]]
            if failed:
                denied = all(r["error"].startswith("not authorised") for r in failed)
                why = "not authorised" if denied else "failed"
                result.undetermined.append(
                    f"{kind} events: {len(failed)} of {len(rows)} day(s) {why}"
                )
            candidates = sorted(
                {
                    str(e.get(EVENT_RECORD_KEY))
                    for e in events
                    if e.get(EVENT_RECORD_KEY) is not None
                    and not pre.declared(
                        kind, str(e.get(EVENT_STATE_KEY) or ""), e.get(EVENT_ACTION_KEY)
                    )
                }
                - seen
            )
            for rid in candidates:
                _fetch_and_check(pre, client, plan, kind, rid, "events")
                seen.add(rid)
    for kind, rid in plan.ids or ():
        if kind is None:
            if len(plan.types) != 1:
                raise InputError(f"id {rid!r} names no type and the run covers {plan.types}")
            kind = plan.types[0]
        else:
            match = [t for t in plan.types if t.casefold() == kind.casefold()]
            if not match:
                result.undetermined.append(f"{kind} ids {rid}: type is not in this run")
                continue
            kind = match[0]
        result.scanned.setdefault(kind, {}).setdefault("ids", 0)
        _fetch_and_check(pre, client, plan, kind, rid, "ids")


def read_ids(file: str | Path) -> list[tuple[str | None, str]]:
    """One id per line, optionally ``<type> <id>``; ``#`` starts a comment."""
    out: list[tuple[str | None, str]] = []
    try:
        lines = Path(file).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InputError(f"cannot read ids file {file}: {exc}") from exc
    for line in lines:
        parts = line.split("#", 1)[0].split()
        if len(parts) == 1:
            out.append((None, parts[0]))
        elif len(parts) == 2:
            out.append((parts[0], parts[1]))
        elif parts:
            raise InputError(f"{file}: expected '<id>' or '<type> <id>', got {line!r}")
    return out
