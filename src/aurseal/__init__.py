"""Scaffold, check and report on test automation projects."""

from __future__ import annotations

# The one place the version is written: pyproject.toml reads it from here
# ([tool.hatch.version]) and the release tag must be testkeel-v<this>.
__version__ = "0.1.0"

# The package was called testkeel up to 0.1.0. Its config directory
# (~/.config/testkeel/) and its release tags (testkeel-v*) are still read as a
# deprecated fallback for one more release.
PREVIOUS_NAME = "testkeel"

__all__ = ["PREVIOUS_NAME", "__version__"]
