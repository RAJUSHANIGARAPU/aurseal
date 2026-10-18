"""Read-only post-deploy smoke checks: a handful of GETs that must answer, and answer in shape.

A post-deploy smoke must be cheap, safe to run against any environment, and it
must **never abstain**: an unreachable service is a red, not a skip, because a
smoke that skips when the backend is down reports green exactly when it
matters. Each :class:`SmokeCheck` is a GET plus a shape predicate; the runner
goes through :class:`oracles.data_health.GetOnlyApi`, so a smoke cannot write.

Typical checks: a version/manifest endpoint answers 200 with a non-blank
version, a listing endpoint answers 200 with an array (shape only — the
content belongs to other tests).
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
