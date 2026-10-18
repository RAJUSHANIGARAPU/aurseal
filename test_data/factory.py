"""Prerequisite test-data factory: provision what a test needs, in dependency order, over the API.

A test that needs "an order for a customer with a price list" should not
depend on those records happening to exist on the environment. The factory
declares each prerequisite as a fixture plus the prerequisites it depends on,
provisions them in order through :class:`test_data.fixture_replay.FixtureSeeder`,
names each one with a per-run tag (``${SEED_TAG}``) so parallel runs never
collide, and caches each one so a bundle provisions shared parents once.

Every result's captured values are passed to dependants as params prefixed
with the prerequisite name, e.g. ``${customer.id}``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from test_data.fixture_replay import Fixture, FixtureSeeder, SeededEntity, SeedError

logger = logging.getLogger(__name__)


def new_seed_tag() -> str:
    """A short run-unique tag for names: 8 hex chars."""
    return uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class Prerequisite:
    name: str
    fixture: Fixture
    depends_on: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)
    after: Callable[[SeededEntity], None] | None = None


class PrerequisiteFactory:
    def __init__(
        self,
        seeder: FixtureSeeder,
        prerequisites: list[Prerequisite],
        *,
        seed_tag: str | None = None,
    ) -> None:
        self._seeder = seeder
        self._defs = {p.name: p for p in prerequisites}
        if len(self._defs) != len(prerequisites):
            raise ValueError("prerequisite names must be unique")
        self.seed_tag = seed_tag or new_seed_tag()
        self._built: dict[str, SeededEntity] = {}

    def order(self, *names: str) -> list[str]:
        """Dependency order for ``names`` (all when empty); raises on a cycle or unknown name."""
        out: list[str] = []
        visiting: set[str] = set()

        def visit(n: str) -> None:
            if n in out:
                return
            if n not in self._defs:
                raise KeyError(f"unknown prerequisite '{n}'")
            if n in visiting:
                raise ValueError(f"dependency cycle through '{n}'")
            visiting.add(n)
            for dep in self._defs[n].depends_on:
                visit(dep)
            visiting.discard(n)
            out.append(n)

        for n in names or tuple(self._defs):
            visit(n)
        return out

    def provision(self, *names: str) -> dict[str, SeededEntity]:
        """Provision ``names`` and their dependencies; already-built ones are reused."""
        for n in self.order(*names):
            if n in self._built:
                continue
            p = self._defs[n]
            params: dict[str, Any] = {"SEED_TAG": self.seed_tag, **p.params}
            for dep in p.depends_on:
                params.update({f"{dep}.{k}": v for k, v in self._built[dep].values.items()})
            try:
                result = self._seeder.replay(p.fixture, params)
            except SeedError as exc:
                raise SeedError(f"prerequisite '{n}' could not be provisioned: {exc}") from exc
            if p.after is not None:
                p.after(result)
            self._built[n] = result
            logger.info("provisioned %s (tag %s)", n, self.seed_tag)
        return {n: self._built[n] for n in (names or tuple(self._defs))}
