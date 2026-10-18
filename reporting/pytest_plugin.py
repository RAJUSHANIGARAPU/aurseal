"""
Session-level report metadata, written by the xdist controller only.

At configure time the run id is pinned (``core.run_id.pin_for_session``) so every
worker spawned afterwards tags its traffic with the same id. At session start the
Allure ``environment.properties``, ``executor.json`` and ``categories.json`` are
written into ``--alluredir``. Workers skip both: they would race to write the same
files with values that are, at best, identical.

Every browser test also carries its engine: a ``browser`` Allure label and a
``("browser", <engine>)`` user property (serialised by xdist to the controller,
where the results store reads it), so a failure on one engine of the matrix reads
as that engine's failure.

Extra Environment rows (checkout provenance, a measured deployed version) come
from ``extra_rows`` callables, each returning a dict; one that raises is logged
and skipped.

Registered from the root ``conftest.py``; elsewhere use ``-p reporting.pytest_plugin``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from core import run_id as run_ids
from core.logger import logger
from gate.browsers import item_browser
from reporting.allure_categories import write_categories
from reporting.allure_environment import environment_rows, executor_info, write_environment
from reporting.step_screenshots import register_if_enabled

PLUGIN_NAME = "report-metadata"
ExtraRows = Callable[[], Mapping[str, str]]


def _option(config: pytest.Config, name: str, default=None):
    try:
        value = config.getoption(name)
    except ValueError:
        return default
    return default if value is None else value


def is_controller(config: pytest.Config) -> bool:
    return not hasattr(config, "workerinput")


class ReportMetadataPlugin:
    def __init__(self, extra_rows: list[ExtraRows] | None = None) -> None:
        self._extra_rows = list(extra_rows or [])
        self.written: list = []

    def pytest_configure(self, config: pytest.Config) -> None:
        if is_controller(config):
            run_ids.pin_for_session()
        register_if_enabled()

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        config = session.config
        results_dir = _option(config, "allure_report_dir")
        if not is_controller(config) or not results_dir:
            return
        env = str(_option(config, "env", "") or "")
        rows = environment_rows(
            env=env or "n/a",
            base_url=self._base_url(env),
            browsers=list(_option(config, "browser", []) or []),
            headed=bool(_option(config, "headed", False)),
            auth_mode=self._auth_mode(env),
            run_id=run_ids.run_id(),
            extra=self._extras(),
        )
        self.written = write_environment(
            results_dir, rows, executor_info(f"Test run - {env or 'n/a'}")
        )
        try:
            self.written.append(write_categories(results_dir))
        except Exception as exc:  # noqa: BLE001 - report metadata never aborts a run
            logger.warning("[allure] could not write categories: %s", exc)

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        tag_browser(item)

    def _extras(self) -> dict[str, str]:
        rows: dict[str, str] = {}
        for provider in self._extra_rows:
            try:
                rows.update({str(k): str(v) for k, v in provider().items()})
            except Exception as exc:  # noqa: BLE001
                logger.warning("[allure] environment row provider failed: %s", exc)
        return rows

    @staticmethod
    def _base_url(env: str) -> str:
        try:
            from core.config_reader import ConfigReader

            return str(ConfigReader(env or None).get("base_url", "n/a"))
        except Exception:  # noqa: BLE001
            return "n/a"

    @staticmethod
    def _auth_mode(env: str) -> str:
        try:
            from core.auth.settings import resolve_auth_mode
            from core.config_reader import ConfigReader

            return resolve_auth_mode(ConfigReader(env or None)).value
        except Exception as exc:  # noqa: BLE001
            return f"unresolved ({exc})"


BROWSER_PROPERTY = "browser"


def tag_browser(item: pytest.Item) -> str | None:
    """Attach the item's engine as a user property and an Allure label; None if no engine."""
    browser = item_browser(item)
    if browser is None:
        return None
    if (BROWSER_PROPERTY, browser) not in item.user_properties:
        item.user_properties.append((BROWSER_PROPERTY, browser))
    try:
        import allure

        allure.dynamic.label(BROWSER_PROPERTY, browser)
    except Exception as exc:  # noqa: BLE001 - report metadata never aborts a run
        logger.debug("[allure] browser label not attached: %s", exc)
    return browser


def register(config: pytest.Config, extra_rows: list[ExtraRows] | None = None) -> None:
    if not config.pluginmanager.hasplugin(PLUGIN_NAME):
        # pytest_configure is a historic hook: pytest calls it on this plugin as
        # it registers, even from inside another plugin's pytest_configure.
        config.pluginmanager.register(ReportMetadataPlugin(extra_rows), PLUGIN_NAME)


def pytest_configure(config: pytest.Config) -> None:
    register(config)
