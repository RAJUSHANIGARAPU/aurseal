"""Locust entry point for the smoke and load profiles: the users, no load shape.

The user classes live in ``src/perf_locust_quickpizza/users.py``, where the offline tests and
mypy see them; Locust picks up every user class this module imports.
"""

from perf_locust_quickpizza.users import SiteUser

__all__ = ["SiteUser"]
