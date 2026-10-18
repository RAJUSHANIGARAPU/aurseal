"""Build UiAutomator2 capabilities from settings, and the driver from those."""

from __future__ import annotations

from pathlib import Path

from appium.options.android import UiAutomator2Options
from appium.webdriver.webdriver import WebDriver

from ..settings import Settings


def build_options(settings: Settings, project_root: Path | None = None) -> UiAutomator2Options:
    """The capabilities for ``settings``. Empty settings are left out, never sent as ``""``."""
    root = project_root or Path.cwd()
    options = UiAutomator2Options()
    options.app = str((root / settings.app_path).resolve())
    options.device_name = settings.device_name
    options.new_command_timeout = settings.new_command_timeout_s
    # Permission dialogs are not what these tests are about; a test for them turns this off.
    options.auto_grant_permissions = True
    if settings.avd:
        options.avd = settings.avd
    if settings.platform_version:
        options.platform_version = settings.platform_version
    return options


def create_driver(settings: Settings, project_root: Path | None = None) -> WebDriver:
    """A new Appium session. Fails before contacting the server when the app is missing."""
    options = build_options(settings, project_root)
    if not Path(str(options.app)).is_file():
        raise FileNotFoundError(f"{options.app} not found: run `make app` or set APP_APP_PATH")
    return WebDriver(settings.appium_url, options=options)
