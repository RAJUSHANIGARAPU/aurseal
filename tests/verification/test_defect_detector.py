"""still_reproduces: every branch, the signature guard, and failure attribution."""

from __future__ import annotations

import pytest

from verification.defect_detector import (
    FIXED_TAG,
    REGRESSED_MARKER,
    Attribution,
    attribute_failure,
    closed_defects,
    require_usable_signature,
    still_reproduces,
)
from verification.verdicts import RedVerdict, read_verdicts

SIGNATURE = r"BUG-142: discount was not applied to the order total"


def broken() -> None:
    raise AssertionError("BUG-142: discount was not applied to the order total (100 != 90)")


def fixed() -> None:
    return None


def control_ok() -> None:
    return None


def verdicts() -> list[str]:
    return [r["verdict"] for r in read_verdicts()]


def test_documented_failure_is_green_and_reproduces():
    assert still_reproduces("BUG-142", SIGNATURE, broken, exercised=control_ok) is (
        RedVerdict.REPRODUCES
    )
    [entry] = read_verdicts()
    assert entry["verdict"] == "reproduces" and entry["ticket"] == "BUG-142"
    assert "100 != 90" in entry["detail"]


def test_passing_oracle_on_an_open_defect_fails_as_cleared():
    with pytest.raises(AssertionError, match=FIXED_TAG):
        still_reproduces("BUG-142", SIGNATURE, fixed, exercised=control_ok)
    assert verdicts() == ["cleared"]


def test_undocumented_failure_fails_and_is_not_the_defect():
    def other() -> None:
        raise AssertionError("BUG-142: setup could not open the order")

    with pytest.raises(AssertionError, match="UNDOCUMENTED"):
        still_reproduces("BUG-142", SIGNATURE, other, exercised=control_ok)
    assert verdicts() == ["signature_mismatch"]


def test_failing_control_skips_and_never_reaches_the_oracle():
    called = []

    def control() -> None:
        raise AssertionError("read returned an empty total")

    with pytest.raises(pytest.skip.Exception, match="has no proof that it reached"):
        still_reproduces(
            "BUG-142", SIGNATURE, lambda: called.append(1) or broken(), exercised=control
        )
    assert called == [] and verdicts() == ["held_empty"]


def test_skipping_control_or_oracle_is_not_exercised():
    with pytest.raises(pytest.skip.Exception):
        still_reproduces("BUG-142", SIGNATURE, broken, exercised=lambda: pytest.skip("no pool"))
    with pytest.raises(pytest.skip.Exception):
        still_reproduces("BUG-142", SIGNATURE, lambda: pytest.skip("no data"), exercised=control_ok)
    assert verdicts() == ["held_empty", "held_empty"]


def test_harness_error_in_oracle_propagates_unchanged():
    error = ConnectionError("server hung up")

    def oracle() -> None:
        raise error

    with pytest.raises(ConnectionError) as caught:
        still_reproduces("BUG-142", SIGNATURE, oracle, exercised=control_ok)
    assert caught.value is error
    [entry] = read_verdicts()
    assert entry["verdict"] == "held_empty" and "harness" in entry["detail"]


def test_pytest_fail_in_oracle_counts_as_an_assertion():
    def oracle() -> None:
        pytest.fail("BUG-142: discount was not applied to the order total")

    assert still_reproduces("BUG-142", SIGNATURE, oracle, exercised=control_ok) is (
        RedVerdict.REPRODUCES
    )


def test_closed_defect_must_pass(monkeypatch):
    monkeypatch.setenv("KNOWN_DEFECTS_CLOSED", "BUG-7, BUG-142")
    assert closed_defects() == {"BUG-7", "BUG-142"}
    assert still_reproduces("BUG-142", SIGNATURE, fixed, exercised=control_ok) is (
        RedVerdict.CLOSED_PASS
    )
    with pytest.raises(AssertionError, match=REGRESSED_MARKER):
        still_reproduces("BUG-142", SIGNATURE, broken, exercised=control_ok)
    assert verdicts() == ["closed_pass", "closed_regressed"]


def test_explicit_closed_overrides_the_environment(monkeypatch):
    monkeypatch.setenv("KNOWN_DEFECTS_CLOSED", "BUG-142")
    assert still_reproduces("BUG-142", SIGNATURE, broken, exercised=control_ok, closed=False) is (
        RedVerdict.REPRODUCES
    )


def test_a_detector_without_a_control_must_say_why():
    with pytest.raises(ValueError, match="no_control_reason"):
        still_reproduces("BUG-142", SIGNATURE, broken, exercised=None)
    verdict = still_reproduces(
        "BUG-142",
        SIGNATURE,
        broken,
        exercised=None,
        no_control_reason="the oracle reads a computed total that is never blank",
    )
    assert verdict is RedVerdict.REPRODUCES


@pytest.mark.parametrize(
    "signature",
    ["", "   ", "BUG-142", "BUG-142:", "(BUG-142) — !!", "BUG-142 OTHER-9 short", "BUG-142 a"],
)
def test_loose_signatures_are_rejected(signature):
    with pytest.raises(ValueError):
        require_usable_signature("BUG-142", signature)
    assert not read_verdicts()


def test_signature_needs_wording_beyond_the_id():
    assert require_usable_signature("BUG-142", r"BUG-142: total mismatch .* cents").search(
        "BUG-142: total mismatch by\n3 cents"
    )
    with pytest.raises(ValueError, match="valid regex"):
        require_usable_signature("BUG-142", "discount ( not applied")
    with pytest.raises(ValueError, match="defect id"):
        require_usable_signature(" ", "discount was not applied")


ENVIRONMENT = [r"locked by another session", r"\b423\b", r"rule is not configured"]
DEFECT = [r"discount ?code"]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("423 Locked: record is locked by another session", Attribution.ENVIRONMENT),
        ("400 rule is not configured", Attribution.ENVIRONMENT),
        ("400 discount code is not valid for this order", Attribution.DEFECT),
        # The environment wins even when the message echoes the defect's field.
        ("423 locked by another session while saving discountCode", Attribution.ENVIRONMENT),
        ("500 internal error", Attribution.UNATTRIBUTABLE),
        ("", Attribution.UNATTRIBUTABLE),
        ("   ", Attribution.UNATTRIBUTABLE),
    ],
)
def test_attribution(message, expected):
    assert attribute_failure(message, defect=DEFECT, environment=ENVIRONMENT) is expected
