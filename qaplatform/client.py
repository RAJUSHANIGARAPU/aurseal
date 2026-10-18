"""PlatformClient: the Python client of the REST API (``requests``, already a dependency).

Every call maps a problem+json answer to a :class:`PlatformError` subclass. Writes are
idempotent on the server (result natural key, ``external_id``, ``Idempotency-Key``), so the
client retries 429/502/503/504 and connection errors with exponential backoff.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from qaplatform.errors import ERRORS_BY_STATUS, PlatformError, RunClosed

RETRY_STATUSES = (429, 502, 503, 504)
BATCH = 1000


class ClientError(PlatformError):
    """Any answer the client could not map to a known problem."""


class PlatformClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        project: str = "default",
        *,
        timeout_s: float = 30.0,
        retries: int = 5,
        backoff_s: float = 0.5,
        session: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._token = token
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    # ------------------------------------------------------------------ transport

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def _p(self, path: str = "") -> str:
        return f"/projects/{self.project}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: bytes | str | None = None,
        content_type: str | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        auth: bool = True,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        if content_type:
            headers["Content-Type"] = content_type
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        while True:
            try:
                resp = self.session.request(
                    method,
                    self._url(path),
                    json=json_body,
                    data=data,
                    params=clean or None,
                    headers=headers,
                    timeout=self.timeout_s,
                )
            except OSError:
                if attempt >= self.retries:
                    raise
                status = None
            else:
                status = resp.status_code
                if status not in RETRY_STATUSES or attempt >= self.retries:
                    break
            delay = self.backoff_s * (2**attempt)
            if status == 429:
                try:
                    delay = max(delay, float(resp.headers.get("Retry-After", 0)))
                except (TypeError, ValueError):
                    pass
            time.sleep(delay)
            attempt += 1
        try:
            payload = resp.json()
        except ValueError:
            payload = {"detail": resp.text}
        if status >= 400:
            raise _error(status, payload)
        return status, payload

    # ------------------------------------------------------------------ discovery

    def health(self) -> dict[str, Any]:
        """Never raises on an unhealthy answer: returns the status document."""
        try:
            _, doc = self.request("GET", "/ready", auth=False)
            return doc
        except PlatformError as exc:
            return {"status": "unavailable", "detail": str(exc)}

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/me")[1]

    # ------------------------------------------------------------------ push

    def create_run(self, name: str, **fields: Any) -> dict[str, Any]:
        body = {"schema_version": "1.0", "name": name}
        body.update({k: v for k, v in fields.items() if v is not None})
        return self.request("POST", self._p("/runs"), json_body=body)[1]

    def push_results(self, run_id: str, results: Sequence[dict[str, Any]]) -> dict[str, int]:
        """Push in batches of 1000; returns summed counts."""
        total = {"received": 0, "added": 0, "duplicates": 0}
        for start in range(0, len(results), BATCH):
            body = {"schema_version": "1.0", "results": list(results[start : start + BATCH])}
            _, doc = self.request("POST", self._p(f"/runs/{run_id}/results"), json_body=body)
            for key in total:
                total[key] += int(doc.get(key, 0))
        return total

    def push_junit(self, run_id: str, path: str | Path) -> dict[str, Any]:
        data = Path(path).read_bytes()
        return self.request(
            "POST",
            self._p(f"/runs/{run_id}/junit"),
            data=data,
            content_type="application/xml",
        )[1]

    def push_verdicts(self, run_id: str, source: str | Path | Sequence[dict[str, Any]]) -> Any:
        """A ``verdicts.jsonl`` path (sent as JSONL) or a list of rows (sent as JSON)."""
        key = str(uuid.uuid4())
        path = f"/runs/{run_id}/verdicts"
        if isinstance(source, (str, Path)):
            text = Path(source).read_text(encoding="utf-8")
            return self.request(
                "POST",
                self._p(path),
                data=text.encode("utf-8"),
                content_type="application/x-ndjson",
                idempotency_key=key,
            )[1]
        return self.request("POST", self._p(path), json_body=list(source), idempotency_key=key)[1]

    def add_evidence(self, run_id: str, links: Sequence[dict[str, Any]]) -> dict[str, Any]:
        body = {"schema_version": "1.0", "links": list(links)}
        return self.request(
            "POST",
            self._p(f"/runs/{run_id}/evidence"),
            json_body=body,
            idempotency_key=str(uuid.uuid4()),
        )[1]

    def finish_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        body = {"schema_version": "1.0", **{k: v for k, v in fields.items() if v is not None}}
        return self.request("POST", self._p(f"/runs/{run_id}/finish"), json_body=body)[1]

    # ------------------------------------------------------------------ read

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.request("GET", self._p(f"/runs/{run_id}"))[1]

    def verdicts(self, **filters: Any) -> Iterator[dict[str, Any]]:
        """Every matching verdict row, following cursors."""
        cursor = None
        while True:
            _, page = self.request(
                "GET", self._p("/verdicts"), params={**filters, "cursor": cursor}
            )
            yield from page.get("verdicts", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def traceability(
        self, *, requirement: str | None = None, release: str | None = None
    ) -> dict[str, Any]:
        params = {"requirement": requirement, "release": release}
        return self.request("GET", self._p("/traceability"), params=params)[1]

    def release_verdict(self, release: str) -> dict[str, Any]:
        return self.request("GET", self._p(f"/releases/{release}/verdict"))[1]

    # ------------------------------------------------------------------ exploratory sessions

    def start_session(self, charter: str, **fields: Any) -> dict[str, Any]:
        body = {"schema_version": "1.0", "charter": charter}
        body.update({k: v for k, v in fields.items() if v not in (None, [], "")})
        return self.request("POST", self._p("/sessions"), json_body=body)[1]

    def session_note(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = {"schema_version": "1.0", **{k: v for k, v in body.items() if v is not None}}
        return self.request("POST", self._p(f"/sessions/{session_id}/notes"), json_body=payload)[1]

    def end_session(
        self, session_id: str, debrief: str = "", *, abandoned: bool = False
    ) -> dict[str, Any]:
        body = {"schema_version": "1.0", "debrief": debrief, "abandoned": abandoned}
        return self.request("POST", self._p(f"/sessions/{session_id}/end"), json_body=body)[1]

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.request("GET", self._p(f"/sessions/{session_id}"))[1]

    # ------------------------------------------------------------------ trigger

    def request_run(self, target: str, **fields: Any) -> dict[str, Any]:
        body = {"schema_version": "1.0", "target": target}
        body.update({k: v for k, v in fields.items() if v is not None})
        return self.request(
            "POST", self._p("/run-requests"), json_body=body, idempotency_key=str(uuid.uuid4())
        )[1]

    def get_run_request(self, request_id: str) -> dict[str, Any]:
        return self.request("GET", self._p(f"/run-requests/{request_id}"))[1]

    def wait_for_run_request(
        self, request_id: str, *, timeout_s: float = 3600, poll_s: float = 10
    ) -> dict[str, Any]:
        """Poll until the request is finished, failed or rejected (or the timeout passes)."""
        deadline = time.monotonic() + timeout_s
        while True:
            doc = self.get_run_request(request_id)
            if doc["status"] in ("finished", "failed", "rejected"):
                return doc
            if time.monotonic() >= deadline:
                return doc
            time.sleep(poll_s)


def _error(status: int, payload: Any) -> PlatformError:
    detail = ""
    errors = None
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or payload.get("title") or "")
        errors = payload.get("errors")
        if status == 409 and str(payload.get("type", "")).endswith("/run-closed"):
            return RunClosed(detail)
    cls = ERRORS_BY_STATUS.get(status)
    if cls is None:
        err = ClientError(f"HTTP {status}: {json.dumps(payload)[:300]}")
        err.status = status
        return err
    return cls(detail, errors=errors)


__all__ = ["ClientError", "PlatformClient"]
