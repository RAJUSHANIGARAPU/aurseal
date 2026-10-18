"""Database testing helpers: read-only probes, before/after state windows, invariants.

    probe = open_probe("sqlite:///reports/app.sqlite")
    with DbState(probe).around({"orders": "SELECT id, status FROM orders"}) as window:
        ...
    window.assert_invariants(InvariantSet(changed("orders")))

See ``docs/features/db-testing.md``.
"""

from checks.db.guard import WriteRefused, read_only_problem, require_read_only
from checks.db.probe import (
    DbProbe,
    QuerySet,
    SqlAlchemyProbe,
    SqliteProbe,
    WritesNotAllowed,
    open_probe,
)
from checks.db.state import DbState, RowDiff, StateWindow, diff_rows

__all__ = [
    "DbProbe",
    "DbState",
    "QuerySet",
    "RowDiff",
    "SqlAlchemyProbe",
    "SqliteProbe",
    "StateWindow",
    "WriteRefused",
    "WritesNotAllowed",
    "diff_rows",
    "open_probe",
    "read_only_problem",
    "require_read_only",
]
