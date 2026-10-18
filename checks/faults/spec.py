"""What fault to inject, where, and when: one vocabulary for the browser and the API side.

``FaultSpec(kind, url_glob, method=None, probability=1.0, after_n=0, times=None,
latency_ms=0, status=503)``

* ``url_glob`` uses Playwright's glob shape: ``**`` matches anything, ``*`` anything but
  ``/``. The same matcher decides for both sides, so a spec means the same thing in a
  browser test and an API test.
* Window: requests that match are numbered from 0. A fault fires from match number
  ``after_n`` on, at most ``times`` times (``None``: no limit), each time with
  ``probability`` drawn from a seeded generator, so a failing run replays exactly.
* Every injected response carries ``x-fault-injected: <spec id>`` so observers (the
  circuit breaker) can tell an injected failure from the environment failing.
"""

from __future__ import annotations

import random
import re
import threading
from dataclasses import dataclass, field
from enum import Enum

HEADER = "x-fault-injected"


class FaultKind(str, Enum):
    LATENCY = "latency"
    STATUS = "status"
    ABORT = "abort"
    TIMEOUT = "timeout"
    TRUNCATE_BODY = "truncate_body"


def glob_to_regex(glob: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass(frozen=True)
class FaultSpec:
    kind: FaultKind | str
    url_glob: str
    method: str | None = None
    probability: float = 1.0
    after_n: int = 0
    times: int | None = None
    latency_ms: int = 0
    status: int = 503
    id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", FaultKind(self.kind))
        if not self.url_glob:
            raise ValueError("a fault needs a url_glob")
        if not 0 <= self.probability <= 1:
            raise ValueError("probability must be within 0..1")
        if self.after_n < 0 or (self.times is not None and self.times < 0):
            raise ValueError("after_n and times cannot be negative")
        if self.kind is FaultKind.STATUS and not 100 <= self.status <= 599:
            raise ValueError("status must be an HTTP status code")
        if self.kind is FaultKind.LATENCY and self.latency_ms <= 0:
            raise ValueError("a latency fault needs latency_ms > 0")
        if not self.id:
            object.__setattr__(
                self, "id", f"{self.kind.value}:{self.method or '*'}:{self.url_glob}"
            )

    def matches(self, method: str, url: str) -> bool:
        if self.method and self.method.upper() != method.upper():
            return False
        return bool(glob_to_regex(self.url_glob).match(url))

    @classmethod
    def from_dict(cls, raw: dict) -> FaultSpec:
        return cls(**raw)


@dataclass
class _Counter:
    seen: int = 0
    fired: int = 0


@dataclass
class FaultPlan:
    """Decides, request by request, which spec (if any) fires. Thread-safe and seeded."""

    specs: tuple[FaultSpec, ...]
    seed: int = 0
    _rng: random.Random = field(init=False, repr=False)
    _counts: dict[str, _Counter] = field(init=False, repr=False)
    _lock: threading.Lock = field(init=False, repr=False, default_factory=threading.Lock)
    active: bool = True

    def __post_init__(self) -> None:
        ids = [s.id for s in self.specs]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate fault ids: {ids}")
        self._rng = random.Random(self.seed)
        self._counts = {s.id: _Counter() for s in self.specs}

    def decide(self, method: str, url: str) -> FaultSpec | None:
        if not self.active:
            return None
        chosen: FaultSpec | None = None
        with self._lock:
            # Every matching spec counts the request, so each spec's window is its own;
            # the first eligible spec (in the order given) is the one applied.
            for spec in self.specs:
                if not spec.matches(method, url):
                    continue
                count = self._counts[spec.id]
                index = count.seen
                count.seen += 1
                if chosen is not None or index < spec.after_n:
                    continue
                if spec.times is not None and count.fired >= spec.times:
                    continue
                if spec.probability < 1 and self._rng.random() >= spec.probability:
                    continue
                count.fired += 1
                chosen = spec
        return chosen

    def fired(self, spec_id: str | None = None) -> int:
        with self._lock:
            if spec_id is not None:
                return self._counts[spec_id].fired
            return sum(c.fired for c in self._counts.values())

    def seen(self, spec_id: str | None = None) -> int:
        with self._lock:
            if spec_id is not None:
                return self._counts[spec_id].seen
            return sum(c.seen for c in self._counts.values())


def is_injected(headers: object) -> bool:
    """True when a response's headers mark it as injected (any mapping, any key case)."""
    if not headers:
        return False
    try:
        items = headers.items()  # type: ignore[attr-defined]
    except AttributeError:
        return False
    return any(str(k).lower() == HEADER for k, _ in items)
