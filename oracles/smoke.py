"""Read-only post-deploy smoke checks: a handful of GETs that must answer, and answer in shape.

A post-deploy smoke must be cheap, safe to run against any environment, and it
must **never abstain**: an unreachable service is a red, not a skip, because a
smoke that skips when the backend is down reports green exactly when it
matters. Each :class:`SmokeCheck` is a GET plus a shape predicate; the runner
goes through :class:`oracles.data_health.GetOnlyApi`, so a smoke cannot write.

Typical checks: a version/manifest endpoint answers 200 with a non-blank
version, a listing endpoint answers 200 with an array (shape only — the
content belongs to other tests).

An anonymous release smoke for a service behind auth needs no token and still
proves the service itself answered:

* :func:`api_document` - the service's published API document answers with
  its own ``info.title`` and the expected GET paths, so the service started and
  registered its handlers (a load balancer's page has neither);
* :func:`auth_challenge` - a protected read answers 401 with a
  ``WWW-Authenticate`` challenge for the scheme, so the request reached the
  service's security chain rather than a proxy's refusal.

Both run through :func:`run_smoke` on an anonymous ``GetOnlyApi``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import requests

from api.clients.http_engine import ApiResponse
from api.engine.context_memory import resolve_path
from oracles.data_health import GetOnlyApi

Expectation = Callable[[ApiResponse], list[str]]


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    path: str
    expect: tuple[Expectation, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)


def status_is(code: int = 200) -> Expectation:
    return lambda r: [] if r.status == code else [f"expected HTTP {code}, got {r}"]


def json_path_non_blank(path: str) -> Expectation:
    def check(r: ApiResponse) -> list[str]:
        try:
            value = resolve_path(r.json(), path)
        except (ValueError, KeyError, IndexError, TypeError):
            return [f"{path} is missing"]
        return [] if value not in (None, "", [], {}) else [f"{path} is blank"]

    return check


def json_path_is_list(path: str, min_items: int = 0) -> Expectation:
    def check(r: ApiResponse) -> list[str]:
        try:
            value = resolve_path(r.json(), path) if path else r.json()
        except (ValueError, KeyError, IndexError, TypeError):
            return [f"{path or 'body'} is missing"]
        if not isinstance(value, list):
            return [f"{path or 'body'} is not a list"]
        return [] if len(value) >= min_items else [f"{path} has {len(value)} < {min_items} items"]

    return check


def _get_paths(document: Any) -> set[str]:
    paths = document.get("paths") if isinstance(document, dict) else None
    if not isinstance(paths, dict):
        return set()
    return {p for p, ops in paths.items() if isinstance(ops, dict) and "get" in ops}


def _schemes(document: dict[str, Any]) -> set[str]:
    components = document.get("components") or {}
    found = set((components.get("securitySchemes") or {}) if isinstance(components, dict) else {})
    found |= set(document.get("securityDefinitions") or {})  # Swagger 2
    return found


def api_document(
    title: str, paths: Iterable[str] = (), security_scheme: str | None = None
) -> Expectation:
    """200, a JSON API document titled ``title``, a GET operation on every path in ``paths``,
    and (when given) ``security_scheme`` declared."""
    wanted = list(paths)

    def check(r: ApiResponse) -> list[str]:
        if r.status != 200:
            return [f"API document: expected HTTP 200, got {r}"]
        try:
            document = r.json()
        except ValueError as exc:
            return [f"API document is not JSON: {exc}"]
        if not isinstance(document, dict):
            return ["API document is not a JSON object"]
        problems = []
        info = document.get("info") if isinstance(document.get("info"), dict) else {}
        if info.get("title") != title:
            problems.append(f"API document title is {info.get('title')!r}, expected {title!r}")
        served = _get_paths(document)
        missing = [p for p in wanted if p not in served]
        if missing:
            problems.append(f"API document lacks GET {missing}")
        if security_scheme and security_scheme not in _schemes(document):
            problems.append(
                f"API document declares no {security_scheme!r} scheme "
                f"(has {sorted(_schemes(document))})"
            )
        return problems

    return check


def auth_challenge(scheme: str = "Bearer") -> Expectation:
    """401 with ``WWW-Authenticate`` starting with ``scheme``: the service's own challenge."""

    def check(r: ApiResponse) -> list[str]:
        if r.status != 401:
            return [f"expected HTTP 401 for an anonymous protected read, got {r}"]
        challenge = (r.header("WWW-Authenticate") or "").strip()
        if not challenge.lower().startswith(scheme.lower()):
            return [
                f"401 without a {scheme} challenge (WWW-Authenticate={challenge!r}): the "
                "refusal may come from something in front of the service"
            ]
        return []

    return check


@dataclass
class SmokeResult:
    name: str
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def run_smoke(api: GetOnlyApi, checks: Iterable[SmokeCheck]) -> list[SmokeResult]:
    """Run every check; transport failures are problems, never skips."""
    results = []
    for check in checks:
        try:
            response = api.get(check.path, check.params or None)
        except requests.exceptions.RequestException as exc:
            results.append(SmokeResult(check.name, [f"unreachable: {exc}"]))
            continue
        expectations = check.expect or (status_is(200),)
        problems = [p for e in expectations for p in e(response)]
        results.append(SmokeResult(check.name, problems))
    return results


def describe(results: Iterable[SmokeResult]) -> str:
    return "\n".join(
        f"{'ok  ' if r.ok else 'FAIL'} {r.name}" + ("" if r.ok else f": {'; '.join(r.problems)}")
        for r in results
    )
