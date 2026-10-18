"""Locust entry point for the stress profile: the same users, driven by ``StepRamp``.

With a load shape in the locustfile, the shape decides the user count and ends the run;
``users``, ``spawn-rate`` and ``run-time`` from the command line no longer apply.
"""

from perf_locust_quickpizza.shapes import StepRamp
from perf_locust_quickpizza.users import SiteUser

__all__ = ["SiteUser", "StepRamp"]
