"""HTTP interaction capture via monkey-patching requests.Session.send.

The patch is installed once at session start and removed at session end — no
per-test setup required.

**Scope — the running test and the threads it started.**
Arming used to live in a bare ``threading.local``, set by
``pytest_runtest_setup`` on the thread pytest runs on. Anything a test handed to
a worker therefore issued its HTTP calls on a thread where the flag had never
been set, and was dropped with no record and no log.
``orchestration/master_orchestrator.py`` runs plugins on a
``ThreadPoolExecutor``, so all plugin traffic was invisible.

Arming the whole process would fix that and buy a worse bug: a daemon thread
outliving the test that started it would have its requests filed under whichever
test happened to be running next. So activation mints a token, and
``Thread.start`` — which runs on the *parent*, the one moment both threads are
identifiable — stamps the child with the token of the thread starting it. A
request is captured when its thread's token is the token of the open session.
Anything else is skipped **and logged**: a request filed under the wrong test is
worse than one nobody recorded.

(An earlier version of this docstring sold thread-locality as what keeps
pytest-xdist workers apart — "captures from gw0 never appear in gw1". xdist
workers are separate processes and never shared this state to begin with.)

**Retention — bounded count, bounded size.**
Response bodies used to be kept whole, and every interaction was held until
``pytest_sessionfinish`` deduplicated them. Ten thousand calls to one endpoint
held ten thousand bodies in order to build one interaction. Duplicates are now
collapsed where they arrive, counted in ``occurrences``, and bodies are clipped
to something schema-equivalent — a contract records the *shape* of a response,
and ``consumer.infer_schema`` reads only types, keys and a list's first element.

Safety guarantees:
- The original ``send`` and ``Thread.start`` are always restored, even on exceptions
- Capture errors are logged, never raised — test behaviour is never affected
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from urllib.parse import urlparse

from contract_testing.consumer import normalize_path
from contract_testing.redaction import redact_headers, redact_query

logger = logging.getLogger(__name__)

# Stamped onto Thread objects rather than held in a threading.local, because the
# stamp has to be written from the parent thread onto the child's object before
# the child exists as a running thread.
_TOKEN_ATTR = "_contract_capture_token"

_original_send = None
_original_thread_start = None
_patch_active = False
_patch_lock = threading.Lock()

_state_lock = threading.RLock()
_session: _Session | None = None

# Headers that add noise without value
_SKIP_REQUEST_HEADERS = frozenset({"user-agent", "accept-encoding", "connection", "content-length"})
_SKIP_RESPONSE_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "connection", "date", "server"}
)

# Ceilings, deliberately far above any response a contract needs to describe, so
# an ordinary body passes through byte-for-byte and only a runaway one is cut.
_MAX_KEYS_PER_TEST = 500  # distinct method+path+status combinations
_MAX_STRING_CHARS = 2_000
_MAX_LIST_ITEMS = 20
_MAX_DEPTH = 20


@dataclass
class _Session:
    """One test's capture. Shared across the threads that test started."""

    token: object
    test_name: str
    interactions: list[dict] = field(default_factory=list)
    index: dict[str, dict] = field(default_factory=dict)
    dropped_keys: int = 0
    warned_threads: set[int] = field(default_factory=set)


def install() -> None:
    """Install the monkey-patch on requests.Session.send (idempotent)."""
    global _original_send, _original_thread_start, _patch_active
    with _patch_lock:
        if _patch_active:
            return
        try:
            import requests

            _original_send = requests.Session.send
            _original_thread_start = threading.Thread.start
            requests.Session.send = _patched_send  # type: ignore[method-assign]
            threading.Thread.start = _patched_thread_start  # type: ignore[method-assign]
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
            if _original_thread_start is not None:
                threading.Thread.start = _original_thread_start  # type: ignore[method-assign]
            _patch_active = False
            logger.debug("[contract] HTTP capture patch removed")
        except ImportError:
            pass


def activate(test_name: str = "") -> None:
    """Open a capture session for this test, and for threads it goes on to start."""
    global _session
    token = object()
    with _state_lock:
        _session = _Session(token=token, test_name=test_name)
    setattr(threading.current_thread(), _TOKEN_ATTR, token)


def deactivate() -> list[dict]:
    """Close the session and return what it collected."""
    global _session
    with _state_lock:
        session = _session
        _session = None
    # Clear the caller's own stamp too: without it, anything this thread sends
    # between teardown and the next setup would be attributed to a closed test.
    setattr(threading.current_thread(), _TOKEN_ATTR, None)
    if session is None:
        return []
    if session.dropped_keys:
        logger.warning(
            "[contract] %s hit more than %d distinct endpoints — %d were not captured",
            session.test_name or "this test",
            _MAX_KEYS_PER_TEST,
            session.dropped_keys,
        )
    return list(session.interactions)


def is_active() -> bool:
    return _live_session() is not None


def _live_session() -> _Session | None:
    """The open session, but only for a thread that belongs to it."""
    session = _session
    if session is None:
        return None
    if getattr(threading.current_thread(), _TOKEN_ATTR, None) is not session.token:
        return None
    return session


# ------------------------------------------------------------------
# Patched send
# ------------------------------------------------------------------


def _patched_thread_start(self):  # noqa: ANN001
    """Hand the child the token of the thread starting it.

    ``start()`` runs on the parent, which is the only point where both threads
    are identifiable — a thread cannot look up its own creator afterwards. This
    is what makes a ThreadPoolExecutor worker part of the test that submitted to
    it, instead of a thread that captures nothing.
    """
    try:
        setattr(self, _TOKEN_ATTR, getattr(threading.current_thread(), _TOKEN_ATTR, None))
    except Exception:  # noqa: BLE001
        logger.debug("[contract] could not stamp thread — its traffic will be skipped")
    return _original_thread_start(self)


def _patched_send(self, request, **kwargs):  # noqa: ANN001
    if _original_send is None:
        raise RuntimeError("capture patch installed but original send is None")
    response = _original_send(self, request, **kwargs)
    session = _live_session()
    if session is not None:
        try:
            _record(session, request, response)
        except Exception:  # noqa: BLE001
            logger.debug("[contract] capture record failed — non-fatal", exc_info=True)
    else:
        _log_unattributed(request)
    return response


def _log_unattributed(request) -> None:
    """Say that a request was dropped, instead of dropping it in silence.

    There was no branch here at all, which is how worker-thread traffic went
    missing for as long as it did. Only the path is logged: a query string
    carries api keys and presigned signatures (see ``redaction.py``), and a
    diagnostic must not become the leak.
    """
    try:
        path = urlparse(request.url).path
        session = _session
        if session is None:
            logger.debug("[contract] no test active — not captured: %s %s", request.method, path)
            return

        ident = threading.get_ident()
        with _state_lock:
            first_time = ident not in session.warned_threads
            session.warned_threads.add(ident)
        if first_time:
            logger.warning(
                "[contract] %s %s came from thread '%s', which %s did not start — "
                "not captured, because recording it would file it under the wrong test",
                request.method,
                path,
                threading.current_thread().name,
                session.test_name or "the running test",
            )
    except Exception:  # noqa: BLE001
        logger.debug("[contract] could not log an unattributed request — non-fatal", exc_info=True)


def _record(session: _Session, request, response) -> None:
    parsed = urlparse(request.url)
    # Keyed on the same normalised path the contract is keyed on, so collapsing
    # here can never drop an interaction the contract would have kept. Status is
    # part of the key: a 500 on an endpoint that usually 200s is signal.
    key = f"{request.method.upper()} {normalize_path(parsed.path)} {response.status_code}"

    if _count_repeat(session, key):
        return

    resp_body = None
    try:
        resp_body = _bound(response.json())
    except Exception:  # noqa: BLE001
        pass

    # Redacted here, at the earliest point, rather than only where the contract
    # is assembled — otherwise a live credential sits in this list in memory for
    # the whole session and any future consumer of a capture inherits the leak.
    interaction = {
        "method": request.method,
        "path": parsed.path,
        "query": redact_query(parsed.query),
        "request_headers": redact_headers(
            _filter_headers(dict(request.headers), _SKIP_REQUEST_HEADERS)
        ),
        "request_body": _parse_body(request.body),
        "status": response.status_code,
        "response_headers": redact_headers(
            _filter_headers(dict(response.headers), _SKIP_RESPONSE_HEADERS)
        ),
        "response_body": resp_body,
        "test_name": session.test_name,
        "occurrences": 1,
    }

    with _state_lock:
        # Re-checked: two threads can reach a new endpoint at the same time, and
        # only one of them may append.
        seen = session.index.get(key)
        if seen is not None:
            seen["occurrences"] += 1
            return
        session.index[key] = interaction
        session.interactions.append(interaction)


def _count_repeat(session: _Session, key: str) -> bool:
    """True when this key is already held (or the test is over its ceiling)."""
    with _state_lock:
        seen = session.index.get(key)
        if seen is not None:
            seen["occurrences"] += 1
            return True
        if len(session.index) >= _MAX_KEYS_PER_TEST:
            session.dropped_keys += 1
            return True
    return False


def _bound(value, depth: int = 0):  # noqa: ANN001, ANN201
    """Clip a parsed body to something a contract cannot tell apart.

    ``consumer.infer_schema`` reads types, dict keys, and only the *first*
    element of a list, so a page of ten thousand rows and its first twenty
    produce the same schema. Keys are never dropped — they are what ``required``
    is built from, and the provider validator checks the retained body against
    that schema.
    """
    if depth > _MAX_DEPTH:
        return None
    if isinstance(value, str):
        return value[:_MAX_STRING_CHARS]
    if isinstance(value, list):
        return [_bound(item, depth + 1) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return {key: _bound(item, depth + 1) for key, item in value.items()}
    return value


def _parse_body(body) -> object:
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            return _bound(json.loads(body.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            return body.decode("utf-8", errors="replace")[:200]
    if isinstance(body, str):
        try:
            return _bound(json.loads(body))
        except ValueError:
            return body[:200]
    return body


def _filter_headers(headers: dict, skip: frozenset[str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in skip}
