"""Turn a ``checks`` engine outcome into a ``PluginResult``.

The plugins under ``plugins/tier*/`` that front a real engine stay thin: build inputs
from the context, call the engine, hand the outcome here. The status decision itself
lives in ``checks._outcome.to_plugin_status`` and nowhere else.
"""

from __future__ import annotations

from typing import Any

from checks._outcome import CheckOutcome, to_plugin_status
from plugins._base_plugin import PluginResult


def plugin_result(outcome: CheckOutcome, **extra: Any) -> PluginResult:
    finding = {**outcome.summary(), **extra}
    return PluginResult(
        status=to_plugin_status(outcome),
        findings=[finding],
        dry_run=outcome.dry_run,
    )
