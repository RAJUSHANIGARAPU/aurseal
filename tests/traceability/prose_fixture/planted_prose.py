"""Fixture for the prose-id check: never collected by the suite (no ``test_`` file name).

One planted violation: ``test_refund_is_rounded`` names REQ-7 in its docstring and links only
REQ-1. Everything else here is linked properly and must not be reported.
"""

import pytest


@pytest.mark.requirement("REQ-1")
def test_refund_is_rounded():
    """Covers REQ-7: the refund rounds half up."""


@pytest.mark.requirement("REQ-2")
def test_req_2_totals_add_up():
    """REQ-2 is linked, so naming it here is fine."""


@pytest.mark.requirement("REQ-3")
@pytest.mark.parametrize("case", [pytest.param(1, id="REQ-3-monthly")])
def test_parametrised(case):
    pass
