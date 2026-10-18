"""Waivers: explicit, owned, expiring; an expired waiver changes nothing."""

from __future__ import annotations

from datetime import date

import pytest

from tests.traceability._release import decide
from tests.traceability._runs import RunSpec, T
from traceability._util import SchemaError
from traceability.release import ReleaseVerdict, Waiver, load_waivers

M = "tests/test_m.py::"
UNPROVED = RunSpec("r1", [T(M + "a", reqs=("REQ-1",))])


def _waiver(expires=date(2026, 12, 31), release="R1"):
    return Waiver(
        "requirement:REQ-1", "@qa-lead", "proof lands in the next sprint", expires, release
    )


def test_a_live_waiver_gives_go_with_waivers_and_is_listed(tmp_path):
    d = decide(tmp_path, [UNPROVED], waivers=[_waiver()])
    assert d.verdict is ReleaseVerdict.GO_WITH_WAIVERS
    assert d.waivers == [_waiver().to_json()]
    assert "waived by @qa-lead" in d.to_markdown()


def test_an_expired_waiver_is_ignored_and_said_so(tmp_path):
    d = decide(tmp_path, [UNPROVED], waivers=[_waiver(expires=date(2026, 1, 1))])
    assert d.verdict is ReleaseVerdict.NO_VERDICT
    assert any("expired 2026-01-01" in r.message for r in d.reasons)


def test_a_waiver_for_another_release_does_not_apply(tmp_path):
    d = decide(tmp_path, [UNPROVED], waivers=[_waiver(release="R2")])
    assert d.verdict is ReleaseVerdict.NO_VERDICT


def test_a_waiver_cannot_hide_an_unwaived_blocker(tmp_path):
    run = RunSpec("r1", [T(M + "a", reqs=("REQ-1",)), T(M + "b", "failed", reqs=("REQ-2",))])
    from tests.traceability._release import reg

    d = decide(
        tmp_path, [run], registry=reg(("REQ-1", "must"), ("REQ-2", "must")), waivers=[_waiver()]
    )
    assert d.verdict is ReleaseVerdict.NO_GO


def test_waivers_file(tmp_path):
    path = tmp_path / "w.yaml"
    path.write_text(
        "waivers:\n  - criterion: requirement:REQ-1\n    owner: '@a'\n"
        "    reason: accepted for this release only\n    expires: 2026-12-31\n"
    )
    assert load_waivers(path)[0].expires == date(2026, 12, 31)
    path.write_text("waivers:\n  - {criterion: x, owner: a, reason: short, expires: 2026-12-31}\n")
    with pytest.raises(SchemaError):
        load_waivers(path)
    assert load_waivers(tmp_path / "missing.yaml") == []
