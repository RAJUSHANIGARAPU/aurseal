"""One logging setup for the whole suite."""

from __future__ import annotations

import logging

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once; pytest's own capture still applies."""
    logging.basicConfig(level=level.upper(), format=FORMAT, force=False)
