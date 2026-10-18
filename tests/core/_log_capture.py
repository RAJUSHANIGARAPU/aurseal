"""
Capturing the framework logger in a test.

The root conftest's session fixture sets ``propagate=False`` on the framework
logger, so ``caplog`` (which listens on the root logger) never sees its records.
This attaches a handler to the framework logger itself for the block.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from core.logger import LOGGER_NAME


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(r.getMessage() for r in self.records)


@contextmanager
def framework_log(level: int = logging.DEBUG):
    logger = logging.getLogger(LOGGER_NAME)
    handler = _ListHandler()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
