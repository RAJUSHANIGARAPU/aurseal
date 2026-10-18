"""
When a run-diagnose-fix loop stops.

The loop wants three green runs in a row. A red run resets that count and costs
one fix round; five rounds is the budget, and an error that comes back unchanged
three rounds running is not being fixed, so the loop stops early instead of
spending the rest. A run that executed no tests is not green.

The state lives in a small JSON file so a command can drive it one run at a time::

    python -m tools.slash.stability --state reports/loop.json reset
    python -m tools.slash.stability --state reports/loop.json record --junit reports/junit.xml

``record`` prints the next step - ``rerun``, ``fix``, ``done`` or ``give-up`` - and
exits 0 for ``rerun``/``fix``, 0 for ``done`` and 3 for ``give-up``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.slash.junit import aggregate

GREENS_NEEDED = 3
MAX_FIX_ROUNDS = 5
SAME_ERROR_ROUNDS = 3

RERUN, FIX, DONE, GIVE_UP = "rerun", "fix", "done", "give-up"


@dataclass
class StabilityLoop:
    greens: int = 0
    fix_rounds: int = 0
    runs: int = 0
    errors: list[str] = field(default_factory=list)  # signature of each red run, in order
    finished: str | None = None

    def record(self, green: bool, error: str = "") -> str:
        """Record one run and return what to do next."""
        if self.finished:
            raise RuntimeError(f"the loop already ended with {self.finished!r}")
        self.runs += 1
        if green:
            self.greens += 1
            if self.greens >= GREENS_NEEDED:
                self.finished = DONE
                return DONE
            return RERUN
        self.greens = 0
        self.errors.append(error)
        recent = self.errors[-SAME_ERROR_ROUNDS:]
        if len(recent) == SAME_ERROR_ROUNDS and len(set(recent)) == 1:
            self.finished = GIVE_UP
            return GIVE_UP
        if self.fix_rounds >= MAX_FIX_ROUNDS:
            self.finished = GIVE_UP
            return GIVE_UP
        self.fix_rounds += 1
        return FIX

    def status(self) -> str:
        return (
            f"green {self.greens} of {GREENS_NEEDED}, fix rounds {self.fix_rounds} of "
            f"{MAX_FIX_ROUNDS}, runs {self.runs}"
        )


def load(path: Path) -> StabilityLoop:
    if not path.exists():
        return StabilityLoop()
    return StabilityLoop(**json.loads(path.read_text(encoding="utf-8")))


def save(loop: StabilityLoop, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(loop), indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive a run-fix loop one run at a time.")
    parser.add_argument("--state", type=Path, default=Path("reports/stability-loop.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset")
    sub.add_parser("show")
    rec = sub.add_parser("record")
    rec.add_argument("--junit", nargs="+", help="JUnit XML of the run just finished")
    rec.add_argument("--green", action="store_true", help="record a green run without XML")
    rec.add_argument("--error", default="", help="signature of a red run without XML")
    args = parser.parse_args(argv)

    if args.command == "reset":
        save(StabilityLoop(), args.state)
        print("loop reset: " + StabilityLoop().status())
        return 0
    loop = load(args.state)
    if args.command == "show":
        print(loop.status())
        return 0
    if args.junit:
        summary = aggregate(args.junit)
        green = summary.measured and summary.total > 0 and summary.failed == 0
        error = "" if green else (summary.signature() or "no tests ran")
    else:
        green, error = args.green, args.error
    step = loop.record(green, error)
    save(loop, args.state)
    print(f"{step}: {loop.status()}")
    return 3 if step == GIVE_UP else 0


if __name__ == "__main__":
    sys.exit(main())
