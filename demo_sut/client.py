"""A small standard-library client for the demo, with the control calls tests need."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode


@dataclass
class DemoResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None

    @property
    def faults(self) -> list[str]:
        """The fault switches the demo reported as active for this response."""
        value = self.headers.get("x-demo-faults", "none")
        return [] if value == "none" else value.split(",")

    @property
    def source(self) -> str | None:
        return self.headers.get("x-demo-source")


@dataclass
class DemoClient:
    """``DemoClient("http://127.0.0.1:8765")``. Paths are relative to the base URL."""

    base_url: str
    timeout_s: float = 10.0
    run_tag: str | None = None
    observed_faults: set[str] = field(default_factory=set)

    def request(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        *,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> DemoResponse:
        data = None
        all_headers = {"Accept": "application/json", **(headers or {})}
        if self.run_tag:
            all_headers.setdefault("X-Test-Run-Id", self.run_tag)
        if form is not None:
            data = urlencode(form).encode()
            all_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            all_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base_url.rstrip("/") + path, data=data, method=method, headers=all_headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as raw:  # noqa: S310
                response = DemoResponse(raw.status, _lower(raw.headers.items()), raw.read())
        except urllib.error.HTTPError as err:
            response = DemoResponse(err.code, _lower(err.headers.items()), err.read())
        self.observed_faults.update(response.faults)
        return response

    def get(self, path: str, **kw: Any) -> DemoResponse:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any = None, **kw: Any) -> DemoResponse:
        return self.request("POST", path, json_body, **kw)

    def patch(self, path: str, json_body: Any = None, **kw: Any) -> DemoResponse:
        return self.request("PATCH", path, json_body, **kw)

    def ok(self, method: str, path: str, json_body: Any = None) -> Any:
        """Request and return the JSON body; raise ``AssertionError`` on a non-2xx answer."""
        response = self.request(method, path, json_body)
        if not 200 <= response.status < 300:
            raise AssertionError(f"{method} {path} -> {response.status}: {response.body[:300]!r}")
        return response.json()

    # -- control -------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return self.ok("GET", "/api/_admin/state")

    def reset(self) -> dict[str, Any]:
        return self.ok("POST", "/api/_admin/reset")

    def freeze(self, instant: str) -> dict[str, Any]:
        return self.ok("POST", "/api/_admin/clock", {"now": instant})

    def shift(self, days: float = 0, seconds: float = 0) -> dict[str, Any]:
        return self.ok("POST", "/api/_admin/clock", {"shift_days": days, "shift_seconds": seconds})

    def real_time(self) -> dict[str, Any]:
        return self.ok("POST", "/api/_admin/clock", {"reset": True})

    def set_faults(self, **switches: bool) -> list[str]:
        return self.ok("POST", "/api/_admin/faults", switches)["active"]

    def replace_faults(self, names: list[str]) -> list[str]:
        return self.ok("POST", "/api/_admin/faults", {"active": list(names)})["active"]

    def settings(self, **changes: Any) -> dict[str, Any]:
        return self.ok("PATCH", "/api/_admin/settings", changes)


def _lower(items: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in items}
