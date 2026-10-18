"""Fault injection: Playwright routing on the browser side, a faulty transport on the API side.

Routing-level injection tests how the *client* handles failure (error shown, no blank page,
bounded retries, recovery), not how a server degrades under real resource exhaustion.
"""

from checks.faults.api import FaultyTransport
from checks.faults.browser import FaultHandle, inject
from checks.faults.spec import HEADER, FaultKind, FaultPlan, FaultSpec, is_injected

__all__ = [
    "HEADER",
    "FaultHandle",
    "FaultKind",
    "FaultPlan",
    "FaultSpec",
    "FaultyTransport",
    "inject",
    "is_injected",
]
