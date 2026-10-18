"""Fault injection in the browser: a Playwright route handler driven by ``FaultSpec``.

``inject(context_or_page, specs, seed=0) -> FaultHandle``. Requests that no spec fires on
fall through untouched (``route.fallback()``), so other route handlers keep working.

* ``status``  — ``route.fulfill`` with the spec's status, a JSON body and ``x-fault-injected``;
* ``abort``   — ``route.abort("connectionreset")``;
* ``timeout`` — ``route.abort("timedout")``;
* ``latency`` — waits ``latency_ms`` (in the route handler), then lets the request through;
* ``truncate_body`` — fetches the real response and fulfils it with half the body.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from checks.faults.spec import HEADER, FaultKind, FaultPlan, FaultSpec, glob_to_regex


class FaultHandle:
    def __init__(self, target: Any, plan: FaultPlan) -> None:
        self.target = target
        self.plan = plan
        self._removed = False

    def _matches(self, url: str) -> bool:
        """URL filter for ``route``; the method is checked per request in ``_handle``."""
        return any(glob_to_regex(spec.url_glob).match(url) for spec in self.plan.specs)

    def _handle(self, route: Any) -> None:
        request = route.request
        spec = self.plan.decide(request.method, request.url)
        if spec is None:
            route.fallback()
            return
        if spec.kind is FaultKind.STATUS:
            route.fulfill(
                status=spec.status,
                headers={HEADER: spec.id, "content-type": "application/json"},
                body=json.dumps({"error": "fault_injected", "message": "Injected fault"}),
            )
        elif spec.kind is FaultKind.ABORT:
            route.abort("connectionreset")
        elif spec.kind is FaultKind.TIMEOUT:
            route.abort("timedout")
        elif spec.kind is FaultKind.LATENCY:
            time.sleep(spec.latency_ms / 1000.0)
            route.fallback()
        else:  # truncate_body
            response = route.fetch()
            body = response.body()
            route.fulfill(
                response=response,
                body=body[: len(body) // 2],
                headers={**response.headers, HEADER: spec.id},
            )

    def fired(self, spec_id: str | None = None) -> int:
        return self.plan.fired(spec_id)

    def seen(self, spec_id: str | None = None) -> int:
        return self.plan.seen(spec_id)

    def remove(self) -> None:
        """Stop injecting. Idempotent."""
        if self._removed:
            return
        self._removed = True
        self.plan.active = False
        try:
            self.target.unroute(self._matches, self._handle)
        except Exception:  # noqa: BLE001 - the page or context may already be closed
            pass


def inject(target: Any, specs: Sequence[FaultSpec], *, seed: int = 0) -> FaultHandle:
    """Install the faults on a Playwright ``BrowserContext`` or ``Page``."""
    handle = FaultHandle(target, FaultPlan(tuple(specs), seed=seed))
    target.route(handle._matches, handle._handle)
    return handle
