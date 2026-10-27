"""Fixture-replay seeder: build test data over the API from ordered JSON request fixtures.

A fixture is the request sequence a user's journey sends, recorded once and
replayed in order::

    {
      "name": "order-to-confirmed",
      "create": {"method": "POST", "path": "/orders", "body": {...},
                 "capture": {"order_id": "id"}},
      "steps": [
        {"method": "PUT", "path": "/orders/${order_id}/lines?version=${version}",
         "body": {"sku": "${sku}", "placed": "${TODAY_TOKEN}"}},
        {"method": "POST", "path": "/orders/${order_id}/confirm?version=${version}",
         "optional": true, "capture": {"invoice_id": "invoice.id"}}
      ]
    }

``${name}`` placeholders are bound, in order of precedence, from values
captured from earlier responses, the caller's ``params``, and built-in run-day
dates: ``TODAY_TOKEN``, ``RUN_DAY_DATE_MINUS_<n>D`` / ``_PLUS_<n>D`` and
``RUN_MONTH_START_PLUS_<n>M`` (the first day of the month ``n`` months ahead).
"Today" is the business timezone's (:func:`core.clock.today`), not the
runner's. A date that must lie in a later month is anchored to the first of
that month, so it never depends on month-end clamping.
An unbound placeholder fails before anything is sent. When ``version_field`` is
set, the entity version is read back from every response and threaded into the
next call as ``${version}``; a version that goes backwards fails loudly.

The seeder never guesses: any non-2xx on a required step raises
:class:`SeedError` naming the step and quoting the response. An ``optional``
step whose status is in ``skip_statuses`` (default 400/404/409/422 — "this
action is not offered in the current state") is skipped and the version
carries forward untouched.

This is deliberately ``${...}`` and not the ``{{...}}`` of
:class:`api.engine.dynamic_data.DynamicDataEngine`: seeding must fail on an
unbound value, never fill it with random data.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from api.clients.http_engine import ApiResponse, HttpEngine
from api.engine.context_memory import resolve_path
from core import clock

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_RUN_DAY_OFFSET = re.compile(r"RUN_DAY_DATE_(MINUS|PLUS)_(\d+)D")
_RUN_MONTH_START = re.compile(r"RUN_MONTH_START_PLUS_(\d+)M")
DEFAULT_SKIP_STATUSES = frozenset({400, 404, 409, 422})


class SeedError(AssertionError):
    """A seed step failed; the message names the step and carries the response."""


@dataclass(frozen=True)
class Step:
    method: str
    path: str
    body: Any = None
    optional: bool = False
    capture: Mapping[str, str] = field(default_factory=dict)
    name: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], index: int) -> Step:
        missing = [k for k in ("method", "path") if not raw.get(k)]
        if missing:
            raise SeedError(f"step {index} is missing {missing}")
        return cls(
            method=str(raw["method"]).upper(),
            path=str(raw["path"]),
            body=raw.get("body"),
            optional=bool(raw.get("optional", False)),
            capture=dict(raw.get("capture", {})),
            name=str(raw.get("name") or f"step {index}"),
        )


@dataclass(frozen=True)
class Fixture:
    name: str
    steps: tuple[Step, ...]
    create: Step | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], name: str = "") -> Fixture:
        create = raw.get("create")
        return cls(
            name=str(raw.get("name") or name),
            create=Step.from_dict({"name": "create", **create}, 0) if create else None,
            steps=tuple(Step.from_dict(s, i + 1) for i, s in enumerate(raw.get("steps", []))),
        )

    @classmethod
    def load(cls, path: str | Path) -> Fixture:
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SeedError(f"cannot load fixture {path}: {exc}") from exc
        return cls.from_dict(raw, path.stem)


@dataclass
class SeededEntity:
    """What a replay produced: every captured value, the last version and response."""

    values: dict[str, Any]
    version: int | None
    last: ApiResponse | None
    skipped: list[str] = field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]


def month_start_plus(day: date, months: int) -> date:
    """The first day of the month ``months`` after ``day``'s month."""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def run_day_values(today: date | None = None) -> Callable[[str], str | None]:
    """Resolver for the built-in date placeholders; 'today' is the business timezone's."""
    day = today or clock.today()

    def resolve(name: str) -> str | None:
        if name == "TODAY_TOKEN":
            return day.isoformat()
        m = _RUN_DAY_OFFSET.fullmatch(name)
        if m:
            sign = -1 if m.group(1) == "MINUS" else 1
            return (day + timedelta(days=sign * int(m.group(2)))).isoformat()
        m = _RUN_MONTH_START.fullmatch(name)
        if m:
            return month_start_plus(day, int(m.group(1))).isoformat()
        return None

    return resolve


def bind(value: Any, values: Mapping[str, Any], builtins: Callable[[str], str | None]) -> Any:
    """Replace every ``${name}`` in strings inside ``value`` (recursively).

    A string that is exactly one placeholder takes the bound value's own type,
    so ``"${count}"`` can become the integer 3. Raises :class:`SeedError` on an
    unbound placeholder.
    """

    def lookup(name: str) -> Any:
        if name in values:
            return values[name]
        builtin = builtins(name)
        if builtin is None:
            raise SeedError(f"placeholder ${{{name}}} has no value")
        return builtin

    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return lookup(whole.group(1))
        return _PLACEHOLDER.sub(lambda m: str(lookup(m.group(1))), value)
    if isinstance(value, list):
        return [bind(v, values, builtins) for v in value]
    if isinstance(value, dict):
        return {k: bind(v, values, builtins) for k, v in value.items()}
    return value


def unbound_placeholders(fixture: Fixture, provided: set[str]) -> set[str]:
    """Placeholders the fixture needs that no param, builtin or earlier capture supplies.

    An offline check: run it over every fixture in a test so a renamed parameter
    fails at collection time rather than half-way through a live seed.
    """
    builtins = run_day_values(date(2000, 1, 1))
    captured: set[str] = set(provided) | {"version"}
    missing: set[str] = set()
    for step in ([fixture.create] if fixture.create else []) + list(fixture.steps):
        text = json.dumps([step.path, step.body])
        for name in _PLACEHOLDER.findall(text):
            if name not in captured and builtins(name) is None:
                missing.add(name)
        captured |= set(step.capture)
    return missing


class FixtureSeeder:
    def __init__(
        self,
        api: HttpEngine,
        *,
        version_field: str | None = None,
        skip_statuses: frozenset[int] = DEFAULT_SKIP_STATUSES,
        today: date | None = None,
    ) -> None:
        self.api = api
        self.version_field = version_field
        self.skip_statuses = skip_statuses
        self._builtins = run_day_values(today)

    def replay(
        self,
        fixture: Fixture,
        params: Mapping[str, Any] | None = None,
        *,
        version: int | None = None,
    ) -> SeededEntity:
        """Run the fixture's create (if any) and steps in order.

        To replay onto an existing entity (a change on something already built),
        pass its identifiers in ``params`` and its current ``version``.
        """
        values: dict[str, Any] = dict(params or {})
        if version is not None:
            values["version"] = version
        result = SeededEntity(values, version, None)
        steps = ([fixture.create] if fixture.create else []) + list(fixture.steps)
        for step in steps:
            response = self._send(step, values)
            if not response.is_success:
                if step.optional and response.status in self.skip_statuses:
                    logger.info(
                        "%s: optional %s skipped (HTTP %d)",
                        fixture.name,
                        step.name,
                        response.status,
                    )
                    result.skipped.append(step.name)
                    continue
                raise SeedError(f"{fixture.name}: {step.name} {step.method} failed: {response}")
            self._capture(fixture, step, response, values)
            self._thread_version(fixture, step, response, values)
            result.last = response
        result.version = values.get("version")
        logger.info(
            "%s: seeded %s", fixture.name, {k: v for k, v in values.items() if k != "version"}
        )
        return result

    # -- read-back helpers ---------------------------------------------------

    def read(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET ``path`` (placeholders bound from ``params``) and return the parsed JSON."""
        response = self.api.get(bind(path, params or {}, self._builtins))
        if not response.is_success:
            raise SeedError(f"read-back of {path} failed: {response}")
        return response.json()

    def read_field(self, path: str, dotted: str, params: Mapping[str, Any] | None = None) -> Any:
        return resolve_path(self.read(path, params), dotted)

    # -- internals -----------------------------------------------------------

    def _send(self, step: Step, values: Mapping[str, Any]) -> ApiResponse:
        path = bind(step.path, values, self._builtins)
        body = bind(copy.deepcopy(step.body), values, self._builtins)
        if step.method in ("GET", "DELETE"):
            return self.api.request(step.method, path)
        return self.api.request(step.method, path, json=body)

    @staticmethod
    def _capture(fixture: Fixture, step: Step, response: ApiResponse, values: dict) -> None:
        if not step.capture:
            return
        try:
            body = response.json()
        except ValueError as exc:
            raise SeedError(
                f"{fixture.name}: {step.name} returned no JSON to capture from"
            ) from exc
        for name, dotted in step.capture.items():
            if dotted == "@location":
                values[name] = response.location_id
                continue
            try:
                values[name] = resolve_path(body, dotted)
            except (KeyError, IndexError, TypeError) as exc:
                raise SeedError(
                    f"{fixture.name}: {step.name} response has no '{dotted}' to capture as {name}"
                ) from exc

    def _thread_version(
        self, fixture: Fixture, step: Step, response: ApiResponse, values: dict
    ) -> None:
        if not self.version_field:
            return
        try:
            nxt = resolve_path(response.json(), self.version_field)
        except (ValueError, KeyError, IndexError, TypeError):
            return
        if not isinstance(nxt, int):
            return
        sent = values.get("version")
        if isinstance(sent, int) and nxt < sent:
            raise SeedError(
                f"{fixture.name}: version went backwards on {step.name}: sent {sent}, got {nxt}"
            )
        values["version"] = nxt
