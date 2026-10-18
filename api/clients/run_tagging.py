"""Mark each outgoing suite request as test traffic under a single per-run id.

This module is an alias of :mod:`core.run_id`, the single source of the run id
for API clients, the browser and the run gate. Every request from
:class:`api.clients.http_engine.HttpEngine` gets an ``X-Test-Run-Id`` header and
a ``User-Agent`` product token; ``TEST_RUN_TAGGING=false`` switches tagging off.
See ``docs/features/config-env.md`` for the resolution order.
"""

from __future__ import annotations

from core.run_id import (
    ENABLED_VARIABLE as ENV_ENABLED,
)
from core.run_id import (
    HEADER,
    MAX_LENGTH,
    browser_user_agent,
    enabled,
    resolve,
    run_id,
    sanitize,
)
from core.run_id import (
    ID_VARIABLE as ENV_ID,
)
from core.run_id import (
    api_headers as request_headers,
)

__all__ = [
    "ENV_ENABLED",
    "ENV_ID",
    "HEADER",
    "MAX_LENGTH",
    "browser_user_agent",
    "enabled",
    "request_headers",
    "resolve",
    "run_id",
    "sanitize",
]
