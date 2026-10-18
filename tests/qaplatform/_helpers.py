"""Helpers shared by the platform tests."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from gate.integrity import Thresholds
from qaplatform.http.routes import PREFIX, Response, handle
from qaplatform.service import PlatformService

# Small floors so tiny runs are judged by every rule; explicit, never read from the env.
THRESHOLDS = Thresholds(min_executed=1, max_skip_ratio=0.5, min_verdicts=1, min_acquired=0)


class Api:
    """Drive the framework-free HTTP pipeline the way a client would."""

    def __init__(self, service: PlatformService, token: str | None) -> None:
        self.service = service
        self.token = token

    def __call__(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        raw: bytes | None = None,
        content_type: str = "application/json",
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        token: str | None = "",
    ) -> Response:
        hdrs = {"Content-Type": content_type, **(headers or {})}
        use = self.token if token == "" else token
        if use:
            hdrs["Authorization"] = f"Bearer {use}"
        data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        return handle(self.service, method, path, query=query, headers=hdrs, body=data)


def new_run(api: Api, project: str = "default", **fields: Any) -> str:
    body = {"schema_version": "1.0", "name": "nightly", **fields}
    resp = api("POST", f"/projects/{project}/runs", body)
    assert resp.status in (200, 201), resp.payload
    return resp.payload["run_id"]


def push(api: Api, run_id: str, results: list[dict[str, Any]], project: str = "default"):
    return api(
        "POST",
        f"/projects/{project}/runs/{run_id}/results",
        {"schema_version": "1.0", "results": results},
    )


def finish(api: Api, run_id: str, project: str = "default", **fields: Any):
    return api(
        "POST", f"/projects/{project}/runs/{run_id}/finish", {"schema_version": "1.0", **fields}
    )


def member(store, user_id: str, project: str = "default", role: str = "tester") -> None:
    """A user bound to ``project`` at ``role`` (what an admin's grant produces)."""
    store.ensure_user(user_id)
    store.scoped(project).grant(user_id, role, "test")


def token_for(store, user_id: str, role: str, project: str = "default") -> str:
    from qaplatform.auth import tokens

    member(store, user_id, project, role)
    raw, _ = tokens.issue(store, user_id=user_id, project_id=project, role=role)
    return raw


class InProcess:
    """A ``requests.Session`` stand-in that serves requests with ``routes.handle``."""

    def __init__(self, service, fail_first: list[int] | None = None) -> None:
        self.service = service
        self.fail_first = list(fail_first or [])
        self.calls = 0

    def request(self, method, url, json=None, data=None, params=None, headers=None, timeout=None):
        self.calls += 1
        if self.fail_first:
            return _Resp(self.fail_first.pop(0), {"title": "busy"}, {"Retry-After": "0"})
        body = data if data is not None else (b"" if json is None else _dumps(json))
        if isinstance(body, str):
            body = body.encode()
        path = urlsplit(url).path[len(PREFIX) :]
        out = handle(
            self.service,
            method,
            path,
            query={k: str(v) for k, v in (params or {}).items()},
            headers=headers or {},
            body=body,
        )
        return _Resp(out.status, out.payload, out.headers)


def _dumps(obj) -> bytes:
    return json.dumps(obj).encode()


class _Resp:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload
