"""Log backends behind one small interface.

``LogBackend.search`` returns the candidate rows for a set of sources in a time window.
The scanner does the boundary matching and the counting, so a backend only has to be
over-inclusive and honest about what it could not see:

* ``truncated``: the backend hit its row limit. Never read as "clean".
* ``missing``: sources that do not exist.
* a :class:`BackendError` (including :class:`BackendTimeout`): nothing can be said.

Credentials never go on a command line: the Loki token is read from an environment
variable and sent as a header, and the ``aws`` CLI reads its own environment or profile.
Error text carries a status or an exception class, never a response body or a log line.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

DEFAULT_ROW_LIMIT = 10_000
DEFAULT_TIMEOUT = 60.0
CLOUDWATCH_MAX_GROUPS = 50


class BackendError(Exception):
    """The backend could not answer."""


class BackendTimeout(BackendError):
    """The query ran past its deadline and was stopped."""


@dataclass
class Row:
    source: str
    text: str = field(repr=False)


@dataclass
class SearchResult:
    rows: list[Row] = field(default_factory=list)
    truncated: bool = False
    missing: list[str] = field(default_factory=list)


class LogBackend(Protocol):
    name: str

    def search(
        self,
        sources: Sequence[str],
        pattern: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> SearchResult: ...


# ---------------------------------------------------------------------------- files


class FilesBackend:
    """Local files and directories (always available; used by tests and demos).

    A source is a file, a directory (searched recursively) or a glob, relative to
    ``root``. A file last modified before ``start`` cannot hold a line from the run and is
    skipped. Lines are not timestamp-filtered: a plain file has no reliable timestamp.
    """

    name = "files"

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root)

    def _files(self, source: str) -> list[Path] | None:
        path = self.root / source
        if path.is_file():
            return [path]
        if path.is_dir():
            return sorted(p for p in path.rglob("*") if p.is_file())
        hits = sorted(Path(h) for h in glob.glob(str(path), recursive=True))
        files = [p for hit in hits for p in (sorted(hit.rglob("*")) if hit.is_dir() else [hit])]
        files = [p for p in files if p.is_file()]
        return files or None

    def search(
        self,
        sources: Sequence[str],
        pattern: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> SearchResult:
        regex = re.compile(pattern)
        deadline = time.monotonic() + timeout
        result = SearchResult()
        for source in sources:
            files = self._files(source)
            if files is None:
                result.missing.append(source)
                continue
            for path in files:
                try:
                    if path.stat().st_mtime < start.timestamp():
                        continue
                    handle = path.open("r", encoding="utf-8", errors="replace")
                except OSError as exc:
                    raise BackendError(f"cannot read a log file: {exc.__class__.__name__}") from exc
                with handle:
                    for count, line in enumerate(handle):
                        if count % 1000 == 0 and time.monotonic() > deadline:
                            raise BackendTimeout(f"file scan ran past {timeout:g}s")
                        if regex.search(line):
                            if len(result.rows) >= limit:
                                result.truncated = True
                                return result
                            result.rows.append(Row(source, line))
        return result


# ---------------------------------------------------------------------------- loki


def _logql_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class LokiBackend:
    """Grafana Loki over its HTTP API (``/loki/api/v1/query_range``), stdlib only.

    A source is a stream selector (default ``{job=~".+"}``). The query is
    ``<selector> |~ "<pattern>"``. A bearer token, when needed, is read from the environment
    variable named by ``token_env``; ``org_id`` becomes ``X-Scope-OrgID``.
    """

    name = "loki"
    DEFAULT_SELECTOR = '{job=~".+"}'

    def __init__(
        self,
        base_url: str,
        *,
        token_env: str | None = None,
        org_id: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("the Loki URL must be http(s)://host[:port]")
        if parsed.username or parsed.password:
            raise ValueError("credentials do not belong in the Loki URL; use a token variable")
        self.base_url = base_url.rstrip("/")
        self.token_env = token_env
        self.org_id = org_id
        self._environ = os.environ if environ is None else environ

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token_env:
            token = self._environ.get(self.token_env)
            if not token:
                raise BackendError(f"environment variable {self.token_env} is not set")
            headers["Authorization"] = f"Bearer {token}"
        if self.org_id:
            headers["X-Scope-OrgID"] = self.org_id
        return headers

    def _query(
        self,
        selector: str,
        pattern: str,
        start: datetime,
        end: datetime,
        limit: int,
        timeout: float,
    ) -> list[str]:
        query = f"{selector} |~ {_logql_string(pattern)}"
        params = urllib.parse.urlencode(
            {
                "query": query,
                "start": int(start.timestamp() * 1e9),
                "end": int(end.timestamp() * 1e9),
                "limit": limit,
                "direction": "forward",
            }
        )
        request = urllib.request.Request(
            f"{self.base_url}/loki/api/v1/query_range?{params}", headers=self._headers()
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise BackendError(f"Loki answered HTTP {exc.code}") from exc
        except TimeoutError as exc:
            raise BackendTimeout(f"Loki query ran past {timeout:g}s") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise BackendTimeout(f"Loki query ran past {timeout:g}s") from exc
            raise BackendError(f"Loki unreachable: {exc.reason.__class__.__name__}") from exc
        except (OSError, ValueError) as exc:
            raise BackendError(f"Loki answer unreadable: {exc.__class__.__name__}") from exc
        try:
            return [v[1] for stream in payload["data"]["result"] for v in stream["values"]]
        except (KeyError, TypeError, IndexError) as exc:
            raise BackendError("Loki answer has an unexpected shape") from exc

    def search(
        self,
        sources: Sequence[str],
        pattern: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> SearchResult:
        result = SearchResult()
        deadline = time.monotonic() + timeout
        for source in sources:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BackendTimeout(f"Loki queries ran past {timeout:g}s")
            lines = self._query(
                source or self.DEFAULT_SELECTOR, pattern, start, end, limit, remaining
            )
            result.rows.extend(Row(source, line) for line in lines)
            if len(lines) >= limit:
                result.truncated = True
        return result


# ---------------------------------------------------------------------------- cloudwatch

Runner = Callable[[Sequence[str], float], "subprocess.CompletedProcess[str]"]


def run_subprocess(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` without a shell; the environment is inherited (that is how ``aws``
    finds its credentials)."""
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)


def _insights_escape(pattern: str) -> str:
    return pattern.replace("/", "\\/")


class CloudWatchBackend:
    """AWS CloudWatch Logs Insights through the ``aws`` CLI.

    A source is a log group name. Groups go to Insights in batches of at most 50 per query.
    ``limit`` is sent as the query limit; reaching it marks the result truncated. When the
    deadline passes the query is stopped (``aws logs stop-query``) and a
    :class:`BackendTimeout` is raised. Credentials come from the CLI's own environment or
    profile and are never passed as arguments.
    """

    name = "cloudwatch"

    def __init__(
        self,
        *,
        region: str | None = None,
        profile: str | None = None,
        runner: Runner = run_subprocess,
        aws_bin: str = "aws",
        poll_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.region = region
        self.profile = profile
        self.runner = runner
        self.aws_bin = aws_bin
        self.poll_seconds = poll_seconds
        self._sleep = sleep
        self._clock = clock

    def _aws(self, args: list[str], timeout: float) -> dict:
        argv = [self.aws_bin, "logs", *args, "--output", "json"]
        if self.region:
            argv += ["--region", self.region]
        if self.profile:
            argv += ["--profile", self.profile]
        try:
            done = self.runner(argv, max(timeout, 1.0))
        except FileNotFoundError as exc:
            raise BackendError("the aws CLI is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendTimeout("an aws CLI call ran past its deadline") from exc
        if done.returncode != 0:
            raise BackendError(f"aws {args[0]} exited with {done.returncode}")
        try:
            return json.loads(done.stdout or "{}")
        except ValueError as exc:
            raise BackendError("the aws CLI answer is not JSON") from exc

    def _stop(self, query_id: str) -> None:
        try:
            self._aws(["stop-query", "--query-id", query_id], 30.0)
        except BackendError:
            pass

    def _run_batch(
        self,
        groups: Sequence[str],
        pattern: str,
        start: datetime,
        end: datetime,
        limit: int,
        deadline: float,
    ) -> tuple[list[Row], bool]:
        query = (
            "fields @log, @message"
            f" | filter @message like /{_insights_escape(pattern)}/"
            f" | sort @timestamp asc | limit {limit}"
        )
        started = self._aws(
            [
                "start-query",
                "--log-group-names",
                *groups,
                "--start-time",
                str(int(start.timestamp())),
                "--end-time",
                str(int(end.timestamp()) + 1),
                "--query-string",
                query,
                "--limit",
                str(limit),
            ],
            max(deadline - self._clock(), 1.0),
        )
        query_id = started.get("queryId")
        if not query_id:
            raise BackendError("start-query returned no query id")
        while True:
            if self._clock() >= deadline:
                self._stop(query_id)
                raise BackendTimeout("the Insights query ran past its deadline and was stopped")
            try:
                answer = self._aws(
                    ["get-query-results", "--query-id", query_id],
                    max(deadline - self._clock(), 1.0),
                )
            except BackendError:
                self._stop(query_id)
                raise
            status = answer.get("status")
            if status == "Complete":
                break
            if status in ("Failed", "Cancelled", "Timeout", "Unknown"):
                raise BackendError(f"the Insights query ended as {status}")
            self._sleep(self.poll_seconds)
        rows = []
        for record in answer.get("results", []):
            fields = {f.get("field"): f.get("value", "") for f in record}
            rows.append(Row(_group_of(fields.get("@log", "")), fields.get("@message", "")))
        return rows, len(rows) >= limit

    def search(
        self,
        sources: Sequence[str],
        pattern: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = DEFAULT_ROW_LIMIT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> SearchResult:
        deadline = self._clock() + timeout
        result = SearchResult()
        names = list(dict.fromkeys(sources))
        for i in range(0, len(names), CLOUDWATCH_MAX_GROUPS):
            rows, truncated = self._run_batch(
                names[i : i + CLOUDWATCH_MAX_GROUPS], pattern, start, end, limit, deadline
            )
            result.rows.extend(rows)
            result.truncated = result.truncated or truncated
        return result


def _group_of(log_field: str) -> str:
    """``123456789012:/aws/group`` -> ``/aws/group`` (the account id never leaves here)."""
    head, sep, tail = log_field.partition(":")
    return tail if sep and head.isdigit() else log_field
