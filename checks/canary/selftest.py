"""Positive control: prove the scanner can see a leak before trusting a CLEARED.

Writes a known canary into a log in a temporary directory, scans it through the files
backend and requires ``STILL_LEAKING``. Three more controls run beside it: a log without
the canary must be ``CLEARED``, a longer token that merely starts with the canary must not
count, and a missing source must be ``NOT_ASSESSED``. If any control disagrees the scanner
is broken and a CLEARED from it means nothing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from checks._outcome import CheckOutcome
from checks.canary.backends import FilesBackend
from checks.canary.canaries import CanaryRun
from checks.canary.scan import KIND, Verdict, scan_targets
from checks.canary.targets import Source, Target

SELFTEST_TAG = "selftest-run"


def run_selftest() -> CheckOutcome:
    run = CanaryRun(SELFTEST_TAG)
    leaked = run.email()
    decoy = run.name()
    artifact = run.finish()
    with tempfile.TemporaryDirectory(prefix="canary-selftest-") as tmp:
        root = Path(tmp)
        (root / "leaky.log").write_text(f"INFO created customer email={leaked}\n", encoding="utf-8")
        (root / "quiet.log").write_text("INFO created customer\n", encoding="utf-8")
        (root / "lookalike.log").write_text(f"INFO ref={decoy}-extra\n", encoding="utf-8")
        targets = [
            Target("leaky", (Source("leaky.log"),)),
            Target("quiet", (Source("quiet.log"),)),
            Target("lookalike", (Source("lookalike.log"),)),
            Target("absent", (Source("absent.log"),)),
        ]
        results = scan_targets(targets, artifact, FilesBackend(root))
    got = {r.target: r.verdict for r in results}
    expected = {
        "leaky": Verdict.STILL_LEAKING,
        "quiet": Verdict.CLEARED,
        "lookalike": Verdict.CLEARED,
        "absent": Verdict.NOT_ASSESSED,
    }
    wrong = sorted(name for name, verdict in expected.items() if got.get(name) is not verdict)
    if wrong:
        return CheckOutcome.breached(
            KIND, f"scanner is broken: wrong verdict for control(s) {', '.join(wrong)}"
        )
    return CheckOutcome.ok(KIND, "positive control reported STILL_LEAKING; negative controls held")
