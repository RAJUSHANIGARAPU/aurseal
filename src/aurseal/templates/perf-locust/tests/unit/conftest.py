"""Offline tests import the user classes and the load shape, but never run a load.

Importing Locust normally monkey-patches the standard library for gevent. Inside pytest
that patch can deadlock with threads pytest plugins have already started (the rerun
plugin's status server is one), so the offline tests skip it. Real runs (``make smoke``)
are not affected: this file is only loaded by pytest.
"""

import os

os.environ.setdefault("LOCUST_SKIP_MONKEY_PATCH", "1")
