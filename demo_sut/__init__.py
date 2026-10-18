"""An invented subscription and billing service to run example tests against.

Standard library only (WSGI + sqlite3 + json; PyYAML, already a project dependency, reads
the lifecycle file). See ``demo_sut/README.md``.
"""

from demo_sut.client import DemoClient
from demo_sut.server import DemoServer

__all__ = ["DemoClient", "DemoServer"]
