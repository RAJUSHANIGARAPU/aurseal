"""
Which build is the environment under test actually running? Measured, once per run.

A failure message that names a cause ("the environment is still on the old build")
without the run having asked the environment is worse than no diagnosis: it ends
the investigation instead of starting one, and every later reader who searches the
log for that version finds the claim confirming itself.

``BuildInfoProbe`` asks a list of candidate endpoints in order and returns
the first real answer. *Absent is a value*: when nothing answers, the result is an
explicit ``UNKNOWN`` that carries why each candidate missed (404, 403, not JSON,
JSON without the field, unreachable). It never raises and never invents a version.

``observe_once`` caches the answer for the process, logs it once, and
``last_observed`` hands it to diagnostics without ever making an HTTP call — a
message composed before any probe ran says "not measured", which sends a reader to
the harness rather than the environment.

Transport is injected (``fetch(path) -> (status, body)``), so every branch is
testable offline and any HTTP client can be plugged in. Candidates are injectable
too; the defaults cover a Spring Boot ``/actuator/info`` and a generic
``/version`` endpoint.

**Stated, not measured.** Some environments expose no version at all. Then
:func:`stated_or_measured` lets the operator state it (``$STATED_BUILD_VERSION``): the
answer carries ``source="stated"``, and :func:`stamp` prefixes every verdict text with
``[version <x> stated, not measured]`` so nobody later reads it as a measurement. A
measured answer always wins over a stated one; with neither the result stays UNKNOWN and
the caller abstains.

**Fingerprints.** When the version cannot be read, the build can sometimes still be told
apart by something only the new build produces. :class:`DocumentFingerprint` reads a
published API document (fetched *anonymously*: pass a fetch without credentials, the
document is public), resolves a JSON pointer in it and reports whether the text there
contains the new build's sentence: ``matched``, ``absent`` or ``unreadable``. Combine it
with :func:`verification.fix_scope.fingerprint_verdict`: asserted where the release is
expected, recorded elsewhere.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.logger import logger

COMPONENT_ENV = "BUILD_INFO_COMPONENT"
DIR_ENV = "BUILD_INFO_DIR"
DEFAULT_COMPONENT = "app"
FILE_NAME = "build-info.json"

Fetch = Callable[[str], "tuple[int, str] | None"]
Reader = Callable[[object], "BuildInfo | None"]


@dataclass(frozen=True)
class BuildInfo:
    source: str | None = None
    value: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        for name in ("source", "value", "detail"):
            raw = getattr(self, name)
            if raw is not None and not str(raw).strip():
                object.__setattr__(self, name, None)

    @classmethod
    def measured(cls, source: str, value: str, detail: str | None = None) -> BuildInfo:
        return cls(source, value, detail)

    @classmethod
    def unknown(cls, reason: str) -> BuildInfo:
        return cls(None, None, reason)

    @classmethod
    def not_measured(cls) -> BuildInfo:
        return cls.unknown("no version probe has run in this process")

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def stated(self) -> bool:
        """The operator asserted this version; nothing measured it."""
        return self.known and self.source == STATED

    def describe(self) -> str:
        """One line that can never be misread as a claim it does not support."""
        if not self.known:
            return f"UNKNOWN — {self.detail or 'no version endpoint answered'}"
        source = self.source or "unnamed source"
        return f"{self.value} [{source}]" + (f" — {self.detail}" if self.detail else "")


def _text(node: object, *path: str) -> str | None:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, (str, int, float)) and not isinstance(node, bool):
        text = str(node).strip()
        return text or None
    return None


def read_actuator_info(body: object) -> BuildInfo | None:
    """``build.version`` from a Spring Boot ``/actuator/info``; git commit as detail."""
    version = _text(body, "build", "version")
    if version is None:
        return None
    parts = [f"service {_text(body, 'build', 'name') or '(unnamed)'}"]
    built = _text(body, "build", "time")
    if built:
        parts.append(f"built {built}")
    commit = _text(body, "git", "commit", "id") or _text(body, "git", "commit")
    if commit:
        parts.append(f"commit {commit}")
    return BuildInfo.measured("build endpoint (GET /actuator/info)", version, ", ".join(parts))


def read_version_field(body: object) -> BuildInfo | None:
    """A top-level ``version`` (or ``versionTag``) field, as many ``/version`` endpoints return."""
    version = _text(body, "version") or _text(body, "versionTag")
    if version is None:
        return None
    detail = ", ".join(
        f"{key} {value}" for key in ("name", "commit", "createdAt") if (value := _text(body, key))
    )
    return BuildInfo.measured("version endpoint (GET /version)", version, detail or None)


@dataclass(frozen=True)
class Candidate:
    path: str
    read: Reader


DEFAULT_CANDIDATES: tuple[Candidate, ...] = (
    Candidate("/version", read_version_field),
    Candidate("/actuator/info", read_actuator_info),
)


def _short(message: str) -> str:
    one_line = " ".join(message.split())
    return one_line if len(one_line) <= 160 else one_line[:160] + "…"


class BuildInfoProbe:
    def __init__(self, fetch: Fetch, candidates: Sequence[Candidate] = DEFAULT_CANDIDATES) -> None:
        self._fetch = fetch
        self._candidates = tuple(candidates)

    def probe(self) -> BuildInfo:
        misses: list[str] = []
        for candidate in self._candidates:
            version, miss = self._attempt(candidate)
            if version is not None:
                return version
            logger.debug("[version] %s", miss)
            misses.append(miss)
        return BuildInfo.unknown("; ".join(misses) or "no candidates configured")

    def _attempt(self, candidate: Candidate) -> tuple[BuildInfo | None, str]:
        path = candidate.path
        try:
            response = self._fetch(path)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a reason
            return (
                None,
                f"{path} -> unreachable ({type(exc).__name__}: {_short(str(exc)) or 'no message'})",
            )
        if response is None:
            return None, f"{path} -> no response"
        status, body = response
        if status != 200:
            return None, f"{path} -> HTTP {status}"
        try:
            parsed = json.loads(body) if body and body.strip() else None
        except ValueError:
            parsed = None
        if parsed is None:
            return None, f"{path} -> HTTP 200 with an unparseable body"
        version = candidate.read(parsed)
        if version is None or not version.known:
            return None, f"{path} -> HTTP 200 but no version field in the body"
        return version, ""


_observed: BuildInfo | None = None
_lock = threading.Lock()


def observe_once(
    probe: BuildInfoProbe,
    *,
    component: str | None = None,
    out_dir: str | Path | None = None,
    run_id: str | None = None,
) -> BuildInfo:
    """Measure on the first call in this process, log it, return the same answer after.

    The first measurement is also written to ``<out_dir>/build-info.json`` (default
    ``$BUILD_INFO_DIR`` or ``reports``) as ``{component, version, source, measured}``, so the
    release verdict can compare what the run measured with the version a candidate pins.
    """
    global _observed
    with _lock:
        if _observed is None:
            _observed = probe.probe()
            logger.info("[version] deployed version: %s", _observed.describe())
            if not _observed.known:
                logger.info(
                    "[version] build version unknown; whatever these diagnostics say about the "
                    "deployed build is a guess, not a measurement"
                )
            write_build_info(_observed, component=component, out_dir=out_dir, run_id=run_id)
        return _observed


def write_build_info(
    info: BuildInfo,
    *,
    component: str | None = None,
    out_dir: str | Path | None = None,
    run_id: str | None = None,
) -> Path | None:
    """Best effort: a failure to write is logged, never raised."""
    target = Path(out_dir or os.environ.get(DIR_ENV) or "reports") / FILE_NAME
    body = {
        "component": component or os.environ.get(COMPONENT_ENV) or DEFAULT_COMPONENT,
        "version": info.value,
        "source": info.source,
        "measured": info.known,
        "detail": info.detail,
        "run_id": run_id or os.environ.get("TEST_RUN_ID") or None,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("[version] could not write %s: %s", target, exc)
        return None
    return target


def last_observed() -> BuildInfo:
    """The measurement taken earlier in this process, or ``not_measured``. Never probes."""
    return _observed if _observed is not None else BuildInfo.not_measured()


def reset_for_tests() -> None:
    global _observed
    with _lock:
        _observed = None


# ---------------------------------------------------------------------------
# stated by the operator
# ---------------------------------------------------------------------------

STATED = "stated"
STATED_ENV = "STATED_BUILD_VERSION"


def stated_or_measured(
    probe_result: BuildInfo,
    stated_env: str = STATED_ENV,
    environ: Mapping[str, str] | None = None,
) -> BuildInfo:
    """The measured version; else the operator's ``$stated_env``; else the UNKNOWN as is."""
    if probe_result.known:
        return probe_result
    stated = ((os.environ if environ is None else environ).get(stated_env) or "").strip()
    if not stated:
        return probe_result
    logger.warning(
        "[version] %s stated by the operator through $%s, not measured (%s)",
        stated,
        stated_env,
        probe_result.detail or "no probe answered",
    )
    return BuildInfo(
        STATED, stated, f"asserted by the operator through ${stated_env}, not measured"
    )


def stamp_prefix(build: BuildInfo) -> str:
    """``[version <x> stated, not measured]`` for a stated build, "" otherwise."""
    return f"[version {build.value} stated, not measured]" if build.stated else ""


def stamp(build: BuildInfo, text: str) -> str:
    """``text``, prefixed when the build version was stated rather than measured."""
    prefix = stamp_prefix(build)
    return f"{prefix} {text}" if prefix else text


# ---------------------------------------------------------------------------
# fingerprint over a published document
# ---------------------------------------------------------------------------


class Match(str, Enum):
    MATCHED = "matched"
    ABSENT = "absent"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class FingerprintReading:
    state: Match
    detail: str

    @property
    def matched(self) -> bool:
        return self.state is Match.MATCHED

    def describe(self) -> str:
        return f"{self.state.value}: {self.detail}"


@dataclass(frozen=True)
class DocumentFingerprint:
    """``contains`` at ``json_pointer`` (RFC 6901) of the JSON document at ``path``."""

    path: str
    json_pointer: str
    contains: str

    def read(self, fetch: Fetch) -> FingerprintReading:
        """Fetch the document (without credentials) and look for the sentence."""
        where = f"{self.path}#{self.json_pointer}"
        try:
            response = fetch(self.path)
        except Exception as exc:  # noqa: BLE001 - every failure is "unreadable"
            return FingerprintReading(Match.UNREADABLE, f"{where} -> unreachable ({exc!r})")
        if response is None:
            return FingerprintReading(Match.UNREADABLE, f"{where} -> no response")
        status, body = response
        if status != 200:
            return FingerprintReading(Match.UNREADABLE, f"{where} -> HTTP {status}")
        try:
            document = json.loads(body)
        except (TypeError, ValueError):
            return FingerprintReading(Match.UNREADABLE, f"{where} -> not JSON")
        try:
            node = json_pointer(document, self.json_pointer)
        except (KeyError, IndexError, ValueError) as exc:
            return FingerprintReading(Match.ABSENT, f"{where} -> pointer not found ({exc})")
        text = node if isinstance(node, str) else json.dumps(node, ensure_ascii=False)
        if self.contains in text:
            return FingerprintReading(Match.MATCHED, f"{where} contains {self.contains!r}")
        return FingerprintReading(Match.ABSENT, f"{where} does not contain {self.contains!r}")


def json_pointer(document: object, pointer: str) -> object:
    """Resolve an RFC 6901 pointer (``/info/description``); ``""`` is the whole document."""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"a JSON pointer starts with '/': {pointer!r}")
    node = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not token.isdigit():
                raise ValueError(f"{token!r} is not a list index")
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise KeyError(token)
    return node


def requests_fetch(base_url: str, session=None, timeout_s: float = 5.0, headers=None) -> Fetch:
    """A ``Fetch`` over ``requests`` against ``base_url`` (imported lazily)."""
    import requests

    client = session or requests.Session()

    def fetch(path: str) -> tuple[int, str]:
        response = client.get(base_url.rstrip("/") + path, timeout=timeout_s, headers=headers)
        return response.status_code, response.text

    return fetch
