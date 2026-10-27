"""Seeded record generation from a constraint profile: valid, boundary, invalid.

* ``valid`` — inside every constraint.
* ``boundary`` — still valid, but at the edges: shortest and longest allowed
  strings, smallest and largest allowed numbers, every choice, and the named
  extremes (right-to-left override, zero-width space, emoji, right-to-left
  script, a 256-character string, 2**53) wherever the profile allows them.
* ``invalid`` — exactly one constraint broken per record, and the record says
  which (``broken_constraint``). Every required string field also gets two
  ``required-empty`` variants: an explicit ``""`` and an all-whitespace value.
  An empty string passes a presence check, so a "required" rule probed only
  with an absent field can still let a blank one through. A named extreme the
  profile forbids for exactly one reason lands here, labelled with that reason.

Every record is reproducible from ``(profile, mode, seed, index)`` alone: record
``i`` draws from its own ``random.Random`` and, when Faker is installed, a Faker
instance re-seeded for that record. Nothing reads the global random state.
Every record is checked against :meth:`Profile.check` before it is returned, so
a generator bug surfaces here rather than as a mislabelled probe result.
"""

from __future__ import annotations

import random
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from test_data.synthetic.profile import FieldSpec, Profile
from test_data.synthetic.regex_sample import UnsupportedPattern, sample

Mode = Literal["valid", "boundary", "invalid"]
MODES: tuple[str, ...] = ("valid", "boundary", "invalid")
MAX_SAFE_INT = 2**53
DEFAULT_MAX_LEN = 24

# Named string extremes: strategy name -> value.
STRING_EXTREMES: dict[str, str] = {
    "rtl_override": "‮‮reversed",
    "zero_width": "Test​User",
    "emoji": "Test\U0001f600User",
    "rtl_script": "יוסף כהן",
    "long_256": "A" * 256,
}

REQUIRED_EMPTY = "required-empty"
"""Strategy label of the explicit empty-string variant (the blank one adds ``-whitespace``)."""
STRING_KINDS = frozenset({"string", "email", "date"})

_WORDS = ("amber", "birch", "cedar", "delta", "ember", "fjord", "garnet", "harbor", "indigo")


@dataclass(frozen=True)
class SyntheticRecord:
    values: dict[str, Any]
    mode: str
    broken_constraint: str | None
    seed: int
    index: int
    strategy: str = "random"

    @property
    def expected(self) -> str:
        return "reject" if self.broken_constraint else "accept"

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": self.values,
            "mode": self.mode,
            "broken_constraint": self.broken_constraint,
            "seed": self.seed,
            "index": self.index,
            "strategy": self.strategy,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class _Case:
    """A deviation from a valid base record: set one field (or drop it)."""

    field: str
    strategy: str
    make: Callable[[random.Random], Any] | None  # None => omit the field
    broken: str | None = None


@dataclass
class SyntheticGenerator:
    seed: int
    locale: str = "en_US"
    _faker: Any = field(default=None, init=False, repr=False)
    faker_available: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        try:
            from faker import Faker
        except ImportError:
            self._faker = None
            self.faker_available = False
        else:
            self._faker = Faker(self.locale)
            self.faker_available = True

    # -- public -------------------------------------------------------------

    def generate(self, profile: Profile, n: int, mode: Mode = "valid") -> list[SyntheticRecord]:
        if mode not in MODES:
            raise ValueError(f"mode {mode!r} not in {MODES}")
        if n < 0:
            raise ValueError("n must be >= 0")
        if mode == "valid":
            return [self.record(profile, "valid", i) for i in range(n)]
        cases = self.cases(profile, mode)
        if not cases:
            return []
        return [self.record(profile, mode, i, cases) for i in range(n)]

    def record(
        self, profile: Profile, mode: str, index: int, cases: list[_Case] | None = None
    ) -> SyntheticRecord:
        """Record ``index`` of ``mode``; identical for identical inputs, in any process."""
        rng = self._rng(mode, index)
        values = self._valid_values(profile, rng)
        strategy, broken = "random", None
        if mode != "valid":
            cases = cases if cases is not None else self.cases(profile, mode)
            case = cases[index % len(cases)]
            strategy, broken = case.strategy, case.broken
            if case.make is None:
                values.pop(case.field, None)
            else:
                values[case.field] = case.make(rng)
        record = SyntheticRecord(values, mode, broken, self.seed, index, strategy)
        _verify(profile, record)
        return record

    def cases(self, profile: Profile, mode: str) -> list[_Case]:
        """The deterministic list of deviations a mode cycles through."""
        if mode == "boundary":
            candidates = [c for spec in profile.fields for c in _boundary_cases(spec)]
            candidates += [c for spec in profile.fields for c in _extreme_cases(spec)]
            return [c for c in candidates if _classify(profile, c) == []]
        if mode == "invalid":
            out = [c for spec in profile.fields for c in _invalid_cases(spec)]
            for case in (c for spec in profile.fields for c in _extreme_cases(spec)):
                broken = _classify(profile, case)
                if len(broken) == 1:  # exactly one constraint broken, or not an invalid case
                    out.append(_Case(case.field, case.strategy, case.make, broken[0]))
            return [c for c in out if _classify(profile, c) == [c.broken]]
        return []

    # -- internals ----------------------------------------------------------

    def _rng(self, mode: str, index: int) -> random.Random:
        # str seeds are hashed with SHA-512 by `random`, independent of PYTHONHASHSEED.
        rng = random.Random(f"{self.seed}:{mode}:{index}")
        if self._faker is not None:
            self._faker.seed_instance(rng.getrandbits(32))
        return rng

    def _valid_values(self, profile: Profile, rng: random.Random) -> dict[str, Any]:
        return {spec.name: self._valid_value(spec, rng) for spec in profile.fields}

    def _valid_value(self, spec: FieldSpec, rng: random.Random) -> Any:
        if spec.kind == "choice":
            return rng.choice(spec.choices)
        if spec.kind == "boolean":
            return rng.random() < 0.5
        if spec.kind in ("integer", "number"):
            low = spec.min if spec.min is not None else 0
            high = spec.max if spec.max is not None else max(low, 0) + 1000
            if spec.kind == "integer":
                return rng.randint(int(_ceil(low)), int(high))
            return round(rng.uniform(low, high), 2)
        if spec.kind == "date":
            return (date(2000, 1, 1) + timedelta(days=rng.randint(0, 11000))).isoformat()
        if spec.kind == "email":
            return f"{self._word(rng)}{rng.randint(1, 999)}@example.test"
        if spec.pattern:
            for _ in range(20):
                value = sample(spec.pattern, rng)
                if _length_ok(spec, value):
                    return value
            raise UnsupportedPattern(f"{spec.name}: no sample of {spec.pattern!r} fits its lengths")
        return _fit(spec, self._text(rng), rng)

    def _word(self, rng: random.Random) -> str:
        if self._faker is not None:
            return "".join(ch for ch in self._faker.user_name() if ch.isalnum()) or "user"
        return rng.choice(_WORDS)

    def _text(self, rng: random.Random) -> str:
        if self._faker is not None:
            return self._faker.name()
        return f"{rng.choice(_WORDS).title()} {rng.choice(_WORDS).title()}"


def _ceil(value: float) -> float:
    return value if float(value).is_integer() else int(value) + 1


def _length_ok(spec: FieldSpec, value: str) -> bool:
    return (spec.min_len is None or len(value) >= spec.min_len) and (
        spec.max_len is None or len(value) <= spec.max_len
    )


def _fit(spec: FieldSpec, text: str, rng: random.Random) -> str:
    low = spec.min_len or (1 if spec.required else 0)
    high = spec.max_len if spec.max_len is not None else max(low, DEFAULT_MAX_LEN)
    if len(text) > high:
        text = text[:high].rstrip() or text[:high]
    while len(text) < low:
        text += rng.choice(string.ascii_lowercase)
    return text


def _filled(length: int) -> Callable[[random.Random], str]:
    return lambda rng: "".join(rng.choice(string.ascii_letters) for _ in range(length))


def _const(value: Any) -> Callable[[random.Random], Any]:
    return lambda rng: value


def _boundary_cases(spec: FieldSpec) -> list[_Case]:
    cases: list[_Case] = []
    if spec.kind == "string" and not spec.pattern:
        if spec.min_len:
            cases.append(_Case(spec.name, "min_len", _filled(spec.min_len)))
        if spec.max_len is not None:
            cases.append(_Case(spec.name, "max_len", _filled(spec.max_len)))
    if spec.kind in ("integer", "number"):
        if spec.min is not None:
            cases.append(_Case(spec.name, "min", _const(spec.min)))
        if spec.max is not None:
            cases.append(_Case(spec.name, "max", _const(spec.max)))
    if spec.kind == "choice":
        cases += [_Case(spec.name, f"choice:{c}", _const(c)) for c in spec.choices]
    if not spec.required:
        cases.append(_Case(spec.name, "omitted", None))
    return cases


def _extreme_cases(spec: FieldSpec) -> list[_Case]:
    if spec.kind == "string":
        return [_Case(spec.name, name, _const(value)) for name, value in STRING_EXTREMES.items()]
    if spec.kind == "integer":
        return [_Case(spec.name, "max_safe_int", _const(MAX_SAFE_INT))]
    return []


def _invalid_cases(spec: FieldSpec) -> list[_Case]:
    name = spec.name
    cases: list[_Case] = []
    if spec.required:
        cases.append(_Case(name, "missing", None, f"{name}.required"))
        if spec.kind in STRING_KINDS:
            blank = " " * max(1, min(3, spec.max_len or 3))
            cases.append(_Case(name, REQUIRED_EMPTY, _const(""), f"{name}.required"))
            cases.append(
                _Case(name, f"{REQUIRED_EMPTY}-whitespace", _const(blank), f"{name}.required")
            )
    wrong_type = "not-a-number" if spec.kind in ("integer", "number", "boolean") else 12345
    if spec.kind != "choice":
        cases.append(_Case(name, "wrong_type", _const(wrong_type), f"{name}.type"))
    if spec.kind == "string":
        if spec.min_len and not (spec.required and spec.min_len == 1):
            # min_len 1 on a required field: "too short" is the empty string, covered above.
            cases.append(_Case(name, "too_short", _filled(spec.min_len - 1), f"{name}.min_len"))
        if spec.max_len is not None:
            cases.append(_Case(name, "too_long", _filled(spec.max_len + 1), f"{name}.max_len"))
        if spec.pattern:
            cases.append(_Case(name, "pattern_mismatch", _breaker(spec), f"{name}.pattern"))
    if spec.kind in ("integer", "number"):
        step = 1 if spec.kind == "integer" else 0.01
        if spec.min is not None:
            cases.append(_Case(name, "below_min", _const(spec.min - step), f"{name}.min"))
        if spec.max is not None:
            cases.append(_Case(name, "above_max", _const(spec.max + step), f"{name}.max"))
    if spec.kind == "choice":
        cases.append(_Case(name, "not_a_choice", _const("__not_a_choice__"), f"{name}.choices"))
    if spec.kind == "email":
        cases.append(_Case(name, "bad_email", _const("not-an-email"), f"{name}.format"))
    if spec.kind == "date":
        cases.append(_Case(name, "bad_date", _const("2024-02-31"), f"{name}.format"))
    return cases


def _breaker(spec: FieldSpec) -> Callable[[random.Random], str]:
    """A value of allowed length that the pattern rejects."""

    def make(rng: random.Random) -> str:
        import re

        base = sample(spec.pattern or "", rng)
        for bad in "!#~ ":
            for candidate in (bad + base[1:], base[:-1] + bad, bad * max(len(base), 1)):
                if _length_ok(spec, candidate) and not re.fullmatch(spec.pattern or "", candidate):
                    return candidate
        return "!"

    return make


def _classify(profile: Profile, case: _Case) -> list[str]:
    """Constraints a case breaks when applied to a neutral valid base."""
    probe = SyntheticGenerator.__new__(SyntheticGenerator)
    probe._faker = None
    rng = random.Random(f"classify:{case.field}:{case.strategy}")
    values = {spec.name: probe._valid_value(spec, rng) for spec in profile.fields}
    if case.make is None:
        values.pop(case.field, None)
    else:
        values[case.field] = case.make(rng)
    return profile.check(values)


def _verify(profile: Profile, record: SyntheticRecord) -> None:
    broken = profile.check(record.values)
    expected = [record.broken_constraint] if record.broken_constraint else []
    if broken != expected:
        raise AssertionError(
            f"generator produced {record.mode} record {record.index} ({record.strategy}) "
            f"breaking {broken}, expected {expected}"
        )
