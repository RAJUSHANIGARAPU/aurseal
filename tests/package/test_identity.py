"""Test identity: explicit ids, normalisation rules and fingerprint stability."""

from __future__ import annotations

import re

import pytest

from testkeel.model.identity import (
    IDENTITY_RULES_VERSION,
    explicit_id,
    fingerprint,
    normalise_path,
    normalise_suite,
    split_variant,
    strip_explicit_id,
)

# Frozen: a change here duplicates every case on a server, so it is a major release.
FROZEN = ("pytest", "tests/api/test_refund.py", "TestRefund", "test_partial")
FROZEN_FINGERPRINT = fingerprint(*FROZEN)


def test_the_fingerprint_is_32_base32_characters_and_deterministic():
    assert re.fullmatch(r"[A-Z2-7]{32}", FROZEN_FINGERPRINT)
    assert all(fingerprint(*FROZEN) == FROZEN_FINGERPRINT for _ in range(5))
    assert IDENTITY_RULES_VERSION == "v1"


def test_the_fingerprint_formula_is_pinned():
    import base64
    import hashlib

    raw = "\x1f".join(["v1", "", "pytest", *FROZEN[1:]]).encode()
    expected = base64.b32encode(hashlib.sha256(raw).digest()[:20]).decode()
    assert FROZEN_FINGERPRINT == expected


@pytest.mark.parametrize(
    "variant",
    [
        ("pytest", "./tests/api/test_refund.py", "TestRefund", "test_partial"),
        ("pytest", "tests\\api\\test_refund.py", "TestRefund", "test_partial"),
        ("PyTest", "tests/api/test_refund.py", "  TestRefund ", "test_partial"),
        ("pytest", "tests/api/test_refund.py", ["shard-3", "TestRefund"], "test_partial"),
        ("pytest", "tests/api/test_refund.py", "[shard 2] TestRefund", "test_partial"),
        ("pytest", "tests/api/test_refund.py", "TestRefund", "test_partial [PAY-123]"),
        ("pytest", "tests/api/test_refund.py", "TestRefund", "@case:PAY-9 test_partial"),
        ("pytest", "tests/api/test_refund.py", "TestRefund", "test_partial\t "),
    ],
)
def test_normalisation_keeps_the_fingerprint(variant):
    assert fingerprint(*variant) == FROZEN_FINGERPRINT


@pytest.mark.parametrize(
    "different",
    [
        ("junit", *FROZEN[1:]),
        ("pytest", "tests/api/Test_refund.py", *FROZEN[2:]),  # N1 keeps case
        ("pytest", FROZEN[1], "TestRefundV2", FROZEN[3]),
        ("pytest", FROZEN[1], FROZEN[2], "test_full"),
    ],
)
def test_a_real_difference_changes_the_fingerprint(different):
    assert fingerprint(*different) != FROZEN_FINGERPRINT


def test_the_project_scopes_the_fingerprint():
    assert fingerprint(*FROZEN, project_id="ACME") != FROZEN_FINGERPRINT


@pytest.mark.parametrize(
    ("title", "tags", "expected"),
    [
        ("partial refund", ["@PAY-123"], "PAY-123"),
        ("partial refund", ["case:PAY-7"], "PAY-7"),
        ("partial refund", ["smoke", "@slow"], None),
        ("[PAY-123] partial refund", [], "PAY-123"),
        ("partial refund @case:PAY-44", [], "PAY-44"),
        ("partial refund @PAY-5", [], "PAY-5"),
        ("mail me at x@PAY-5", [], None),
        ("partial refund pay-123", [], None),
        ("[PAY-1] title", ["@PAY-2"], "PAY-2"),
    ],
)
def test_explicit_ids(title, tags, expected):
    assert explicit_id(title, tags) == expected


def test_stripping_the_explicit_id_normalises_the_title():
    assert strip_explicit_id("[PAY-123]  partial   refund") == "partial refund"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("test_x[EUR-10]", ("test_x", "EUR-10")),
        ("test_eval[6*9-42]", ("test_eval", "6*9-42")),
        ("test_x[]", ("test_x", "")),
        ("test_x", ("test_x", None)),
        ("[PAY-1] refund", ("[PAY-1] refund", None)),
    ],
)
def test_parameter_ids_are_a_variant(name, expected):
    assert split_variant(name) == expected


def test_path_and_suite_normalisation():
    assert normalise_path(".//tests//a.py") == "tests/a.py"
    assert normalise_suite(["gw0", "Refunds", "  partial  "]) == "Refunds › partial"
