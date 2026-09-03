"""
What a pytest exit code is allowed to mean.

``e2e_playwright`` and ``api_contract`` both shell out to pytest and both read
``returncode == 0`` as ``pass`` and *everything else* as ``fail``. Four of
pytest's six exit codes are not test failures at all:

* **5** — no tests were collected. The suite ran and verified nothing, which is
  ``unknown``. Reported as ``fail`` it looks like a product defect; reported as
  ``pass`` (had the code been written the other way) it looks like health.
* **2, 3, 4** — interrupted, internal error, and "pytest rejected its own
  command line". These are the harness, not the product. ``api_contract``'s
  default test path was relative, so running the orchestrator from anywhere but
  the repository root produced exit 4 and published it as a contract failure.

Only exit 1 means what the old code assumed: tests ran, tests failed.
"""

from __future__ import annotations

from plugins._base_plugin import PluginStatus

_BY_CODE: dict[int, tuple[str, str]] = {
    0: (PluginStatus.PASS.value, "the suite ran and every test passed"),
    1: (PluginStatus.FAIL.value, "the suite ran and reported test failures"),
    2: (PluginStatus.ERROR.value, "pytest was interrupted before it finished"),
    3: (PluginStatus.ERROR.value, "pytest hit an internal error"),
    4: (PluginStatus.ERROR.value, "pytest rejected its own command line"),
    5: (PluginStatus.UNKNOWN.value, "pytest collected no tests, so nothing was verified"),
}


def status_for_exit_code(code: int) -> tuple[str, str]:
    """Return ``(status, reason)`` for a pytest exit code.

    An unrecognised code is ``unknown``, not ``fail``: a runner that exited in a
    way we have never seen has not told us the product is broken.
    """
    return _BY_CODE.get(
        code, (PluginStatus.UNKNOWN.value, f"pytest exited {code}, which has no defined meaning")
    )
