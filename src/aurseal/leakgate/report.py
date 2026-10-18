"""The only leak-gate module that prints. Locations and rule ids, never matched text."""

from __future__ import annotations

from typing import TextIO

from aurseal.leakgate.gate import CANNOT_RUN, NOT_ASSESSED, Outcome


def emit(outcome: Outcome, out: TextIO, err: TextIO) -> None:
    for note in outcome.notes:
        print(f"leakgate: {note}", file=err)
    if outcome.code == CANNOT_RUN:
        print(f"leakgate: cannot run: {outcome.error}", file=err)
        return
    for finding in outcome.findings:
        print(finding, file=out)
    if outcome.findings:
        places = len({f.location for f in outcome.findings})
        print(
            f"leakgate: {len(outcome.findings)} finding(s) in {places} location(s)",
            file=err,
        )
    elif outcome.code == NOT_ASSESSED:
        print("leakgate: built-in rules clean; denylist NOT ASSESSED", file=err)
    else:
        print("leakgate: clean", file=err)
