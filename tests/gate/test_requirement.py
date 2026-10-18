"""Skip-or-fail policy and external-checkout binding."""

from __future__ import annotations

import pytest

from gate.requirement import (
    SUITE,
    VERDICT,
    Requirement,
    require_checkout,
    require_or_skip,
    require_verdict_or_skip,
    resolve_checkout,
)


def test_usable_returns():
    require_or_skip(True, "fine")


def test_unusable_skips_by_default():
    with pytest.raises(pytest.skip.Exception, match="renderer unreachable"):
        require_or_skip(False, "renderer unreachable")


def test_unusable_fails_when_the_lane_requires_it(monkeypatch):
    monkeypatch.setenv("SUITE_REQUIRED", "true")
    with pytest.raises(pytest.fail.Exception, match="this lane requires it: renderer unreachable"):
        require_or_skip(False, "renderer unreachable")


def test_lanes_do_not_arm_each_other(monkeypatch):
    monkeypatch.setenv("SUITE_REQUIRED", "true")
    assert SUITE.required() and not VERDICT.required()
    with pytest.raises(pytest.skip.Exception, match="CASE-7 - no fixture"):
        require_verdict_or_skip("CASE-7", "no fixture")
    monkeypatch.setenv("VERDICT_REQUIRED", "1")
    with pytest.raises(pytest.fail.Exception, match="CASE-7 - a verdict could not run"):
        require_verdict_or_skip("CASE-7", "no fixture")


@pytest.mark.parametrize(("raw", "expected"), [("yes", True), ("0", False), ("maybe", False)])
def test_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("CUSTOM_REQUIRED", raw)
    assert Requirement("CUSTOM_REQUIRED").required() is expected


def test_explicit_strict_overrides_the_environment():
    with pytest.raises(pytest.fail.Exception):
        Requirement("UNSET_FLAG").require_or_skip(False, "x", strict=True)


def test_resolve_checkout_diagnostics(tmp_path, monkeypatch):
    assert "is not set and there is no fallback" in resolve_checkout("CFG_PATH").diagnostic
    monkeypatch.setenv("CFG_PATH", str(tmp_path / "gone"))
    assert "does not exist" in resolve_checkout("CFG_PATH").diagnostic
    monkeypatch.setenv("CFG_PATH", str(tmp_path))
    half = resolve_checkout("CFG_PATH", expected=("flows", "settings.yml"))
    assert not half.usable and "half-populated: missing flows, settings.yml" in half.diagnostic
    (tmp_path / "flows").mkdir()
    (tmp_path / "settings.yml").write_text("")
    assert resolve_checkout("CFG_PATH", expected=("flows", "settings.yml")).usable
    monkeypatch.setenv("CFG_PATH", "~no_such_user_for_this_test/cfg")
    assert "not a usable path" in resolve_checkout("CFG_PATH").diagnostic


def test_resolve_checkout_fallback(tmp_path):
    resolution = resolve_checkout("CFG_PATH", fallback=tmp_path)
    assert resolution.usable and resolution.diagnostic.startswith("fallback")


def test_require_checkout_abstains_unless_required(tmp_path, monkeypatch):
    with pytest.raises(pytest.skip.Exception, match="CFG_PATH is not set"):
        require_checkout("CFG_PATH", "CFG_REQUIRED")
    monkeypatch.setenv("CFG_REQUIRED", "true")
    with pytest.raises(pytest.fail.Exception, match="binding to the external checkout"):
        require_checkout("CFG_PATH", "CFG_REQUIRED")
    monkeypatch.setenv("CFG_PATH", str(tmp_path))
    assert require_checkout("CFG_PATH", "CFG_REQUIRED") == tmp_path.resolve()
