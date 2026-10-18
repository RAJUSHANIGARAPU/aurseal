"""Fixtures for the demo service's own tests: one demo instance per session (per xdist worker)."""

from demo_sut.pytest_plugin import (  # noqa: F401 - fixtures are used by name
    demo_control,
    demo_http,
    demo_seed,
    demo_sut,
)
