"""``python -m checks.mutation [--package NAME ...] [--config FILE]``.

Exit 0 when every selected package reached its threshold, 1 when one fell below, 2 when a
package could not be scored (mutmut missing, its run failed, nothing generated).
"""

from __future__ import annotations

import argparse
import json
import sys

from checks._outcome import CheckOutcome, exit_code
from checks._sink import JsonlSink
from checks.mutation.config import DEFAULT_CONFIG, MutationConfig
from checks.mutation.judge import KIND
from checks.mutation.runner import run_mutation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m checks.mutation")
    parser.add_argument("--package", action="append", default=None)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = MutationConfig.from_pyproject(args.config)
    except ValueError as exc:
        outcome = CheckOutcome.error(KIND, f"configuration rejected: {exc}")
    else:
        outcome = run_mutation(config, args.package, sink=None if args.no_record else JsonlSink())
    print(json.dumps(outcome.summary(), indent=2, default=str))
    return exit_code(outcome)


if __name__ == "__main__":
    sys.exit(main())
