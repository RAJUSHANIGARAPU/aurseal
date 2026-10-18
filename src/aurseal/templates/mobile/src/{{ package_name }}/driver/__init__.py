"""The Appium session: capabilities come from settings, in one place."""

from .factory import build_options, create_driver

__all__ = ["build_options", "create_driver"]
