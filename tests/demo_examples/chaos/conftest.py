"""Fault-injection examples: the ``faults`` fixture and its not-exercised rule for this folder.

Hooks in a conftest apply to the tests under it, so these examples get the rule without the
plugin being registered globally (``-p checks.faults.pytest_plugin`` does the same anywhere).
"""

from checks.faults.pytest_plugin import (  # noqa: F401 - fixture and hooks used by name
    faults,
    pytest_configure,
    pytest_runtest_makereport,
)
