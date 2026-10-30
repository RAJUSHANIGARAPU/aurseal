"""Seeded canary log scan: find personal data in service logs with synthetic canaries.

Every run seeds entities whose name, email and account fields carry run-unique values
(``CanaryRun``). After the run, ``scan.scan_targets`` searches the services' logs for those
exact values in the run's time window. Only test data carries them, so a hit is proof.
"""

from checks.canary.canaries import Canary, CanaryRun, RunArtifact, load_artifacts
from checks.canary.scan import Verdict, scan_targets
from checks.canary.targets import Source, Target, load_targets

__all__ = [
    "Canary",
    "CanaryRun",
    "RunArtifact",
    "Source",
    "Target",
    "Verdict",
    "load_artifacts",
    "load_targets",
    "scan_targets",
]
