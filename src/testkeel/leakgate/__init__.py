"""Leak gate: keep private names and personal data out of a public tree.

Rules: the private denylist (``deny.term``/``deny.qual``/``deny.re``) and built-in
personal-data rules (``pii.email``, ``pii.iban``, ``pii.phone``, ``pii.home``), plus, for a
generated project only, the :mod:`.project` rules. Output is a location and rule id per
finding, never the matched text. See :mod:`.gate` for exit codes.
"""

from testkeel.leakgate.gate import CANNOT_RUN, CLEAN, FINDINGS, NOT_ASSESSED, Outcome, run
from testkeel.leakgate.project import ProjectRules

__all__ = ["CANNOT_RUN", "CLEAN", "FINDINGS", "NOT_ASSESSED", "Outcome", "ProjectRules", "run"]
