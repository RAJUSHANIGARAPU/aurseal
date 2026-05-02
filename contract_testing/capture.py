"""HTTP interaction capture via monkey-patching requests.Session.send.

Thread-local state ensures that concurrent test workers don't cross-contaminate
each other's captures. The patch is installed once at session start and removed
at session end — no per-test setup required.

Safety guarantees:
- Original `send` is always restored (even on exceptions)
- Capture errors are logged, never raised — test behaviour is never affected
- Thread-local: captures from worker gw0 never appear in gw1
"""

from __future__ import annotations

import json
import logging
import threading
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_local = threading.local()
_original_send = None
_patch_active = False
_patch_lock = threading.Lock()

# Headers that add noise without value
_SKIP_REQUEST_HEADERS = frozenset({"user-agent", "accept-encoding", "connection", "content-length"})
_SKIP_RESPONSE_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "connection", "date", "server"}
)


def install() -> None:
    """Install the monkey-patch on requests.Session.send (idempotent)."""
    global _original_send, _patch_active
    with _patch_lock:
        if _patch_active:
            return
        try:
            import requests

            _original_send = requests.Session.send
            requests.Session.send = _patched_send  # type: ignore[method-assign]
            _patch_active = True
            logger.debug("[contract] HTTP capture patch installed")
        except ImportError:
            logger.warning("[contract] requests not available — capture disabled")


def remove() -> None:
    """Remove the monkey-patch and restore original behaviour."""
    global _patch_active
    with _patch_lock:
        if not _patch_active or _original_send is None:
            return
        try:
            import requests

            requests.Session.send = _original_send  # type: ignore[method-assign]
            _patch_active = False
            logger.debug("[contract] HTTP capture patch removed")
        except ImportError:
            pass


def activate(test_name: str = "") -> None:
    """Enable capture for the current thread."""
    _local.active = True
    _local.test_name = test_name
    _local.interactions = []


def deactivate() -> list[dict]:
    """Disable capture and return collected interactions for this thread."""
    interactions = list(getattr(_local, "interactions", []))
    _local.active = False
    _local.test_name = ""
    _local.interactions = []
    return interactions


def is_active() -> bool:
    return getattr(_local, "active", False)


# ------------------------------------------------------------------
# Patched send
# ------------------------------------------------------------------


def _patched_send(self, request, **kwargs):  # noqa: ANN001
    if _original_send is None:
        raise RuntimeError("capture patch installed but original send is None")
    response = _original_send(self, request, **kwargs)
    if is_active():
        try:
            _record(request, response)
        except Exception:  # noqa: BLE001
            logger.debug("[contract] capture record failed — non-fatal", exc_info=True)
    return response


def _record(request, response) -> None:
    parsed = urlparse(request.url)
    req_body = _parse_body(request.body)
    resp_body = None
    try:
        resp_body = response.json()
    except Exception:  # noqa: BLE001
        pass

    interaction = {
        "method": request.method,
        "path": parsed.path,
        "query": parsed.query or "",
        "request_headers": _filter_headers(dict(request.headers), _SKIP_REQUEST_HEADERS),
        "request_body": req_body,
        "status": response.status_code,
        "response_headers": _filter_headers(dict(response.headers), _SKIP_RESPONSE_HEADERS),
        "response_body": resp_body,
        "test_name": getattr(_local, "test_name", ""),
    }
    if not hasattr(_local, "interactions"):
        _local.interactions = []
    _local.interactions.append(interaction)


def _parse_body(body) -> object:
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return body.decode("utf-8", errors="replace")[:200]
    if isinstance(body, str):
        try:
            return json.loads(body)
        except ValueError:
            return body[:200]
    return body


def _filter_headers(headers: dict, skip: frozenset[str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in skip}
