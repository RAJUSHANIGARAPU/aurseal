"""Synthetic records as factory prerequisite parameters."""

from __future__ import annotations

from dataclasses import dataclass

from test_data.synthetic.generator import SyntheticGenerator, SyntheticRecord
from test_data.synthetic.profile import Profile


@dataclass(frozen=True)
class SyntheticParams:
    """What a factory prerequisite needs to regenerate the exact record it used.

    ``Prerequisite(..., params=SyntheticParams(profile, "boundary", 7, 3).as_params())``
    puts the record's values into the fixture parameters, plus the coordinates
    ``(entity, mode, seed, index)`` that reproduce it.
    """

    profile: Profile
    mode: str = "valid"
    seed: int = 0
    index: int = 0

    def record(self) -> SyntheticRecord:
        generator = SyntheticGenerator(self.seed)
        if self.mode == "valid":
            return generator.record(self.profile, "valid", self.index)
        return generator.generate(self.profile, self.index + 1, self.mode)[self.index]  # type: ignore[arg-type]

    def as_params(self) -> dict:
        record = self.record()
        return {
            **record.values,
            "SYNTHETIC": f"{self.profile.entity}:{self.mode}:{self.seed}:{self.index}",
        }
