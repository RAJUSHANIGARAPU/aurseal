"""Side-effect guard: opt-in, allowlist, one id, denylist, fresh precheck, cap, abort."""

from __future__ import annotations

import json
import textwrap

import pytest

from gate.side_effect import SideEffectGuard

pytest_plugins = ["pytester"]

ON = {"SEND_PROBE": "true"}


def guard(tmp_path, **overrides) -> SideEffectGuard:
    settings = dict(
        flag_env="SEND_PROBE",
        allowed_envs={"qa", "production"},
        max_calls=2,
        ceiling=5,
        protected_ids={"SUB-000001"},
        evidence_dir=tmp_path,
        environ=ON,
    )
    settings.update(overrides)
    made = SideEffectGuard(**settings)
    if made.skip_reason("qa") is None:
        made.check_env("qa")
    return made


def ok(_record_id):
    return 202, {"queued": True}


def fresh(_before):
    return None


def test_off_by_default_and_production_refused_even_when_listed(tmp_path):
    off = SideEffectGuard("SEND_PROBE", {"qa"}, 1, 1, environ={})
    assert "opt-in" in off.skip_reason("qa")
    on = SideEffectGuard("SEND_PROBE", {"qa", "production"}, 1, 1, environ=ON)
    assert "production name" in on.skip_reason("production")
    assert "allowlist" in on.skip_reason("staging")
    assert on.skip_reason("QA") is None
    with pytest.raises(pytest.skip.Exception, match="production name"):
        on.check_env("prod")


def test_no_call_before_the_environment_check(tmp_path):
    unchecked = SideEffectGuard("SEND_PROBE", {"qa"}, 1, 1, environ=ON, evidence_dir=tmp_path)
    outcome = unchecked.call("SUB-000123", fresh, ok)
    assert not outcome.sent and "check_env" in outcome.refused


@pytest.mark.parametrize("bad", ["SUB-1,SUB-2", "SUB-1 SUB-2", " SUB-1", "SUB-1;", "", "a/b"])
def test_exactly_one_canonical_id(tmp_path, bad):
    sent = []
    outcome = guard(tmp_path).call(bad, fresh, lambda i: sent.append(i) or (200, None))
    assert not outcome.sent and "canonical id" in outcome.refused
    assert sent == []


def test_protected_and_related_ids_are_refused(tmp_path):
    g = guard(tmp_path, related=lambda i: {"SUB-000001"} if i == "SUB-000077" else set())
    assert "is protected" in g.call("SUB-000001", fresh, ok).refused
    assert "shares a resource with protected SUB-000001" in g.call("SUB-000077", fresh, ok).refused
    assert g.call("SUB-000123", fresh, ok).sent


def test_precheck_judges_a_fresh_read_taken_right_before_the_call(tmp_path):
    reads = iter([{"status": "active"}, {"status": "active"}, {"status": "cancelled"}])
    g = guard(tmp_path)

    def precheck(before):
        return None if before["status"] == "active" else f"status {before['status']}"

    first = g.call("SUB-000123", precheck, ok, read=lambda _i: next(reads))
    assert first.sent and first.before == {"status": "active"}
    second = g.call("SUB-000124", precheck, ok, read=lambda _i: next(reads))
    assert not second.sent and second.refused == "precondition: status cancelled"


def test_cap_never_exceeds_the_ceiling(tmp_path):
    g = guard(tmp_path, max_calls=10, ceiling=2)
    assert g.budget == 2
    results = [g.call(f"SUB-00012{n}", fresh, ok).sent for n in range(3)]
    assert results == [True, True, False]
    assert "budget spent (2 of 2" in g.calls[-1].refused


def test_first_non_2xx_aborts_every_later_call(tmp_path):
    g = guard(tmp_path, max_calls=5)
    failed = g.call("SUB-000123", fresh, lambda _i: (500, "boom"))
    assert failed.sent and not failed.succeeded
    later = g.call("SUB-000124", fresh, ok)
    assert not later.sent and later.refused.startswith("run aborted: SUB-000123 answered 500")


def test_transport_error_counts_as_a_failed_call(tmp_path):
    def broken(_i):
        raise ConnectionError("reset")

    g = guard(tmp_path)
    assert not g.call("SUB-000123", fresh, broken).succeeded
    assert g.aborted


def test_every_sent_call_writes_before_answer_after(tmp_path):
    state = {"outbox": 0}

    def send(_i):
        state["outbox"] += 1
        return 202, {"queued": True}

    attached = []

    class Item:
        user_properties = attached

    g = guard(tmp_path, request=Item())
    outcome = g.call("SUB-000123", fresh, send, read=lambda _i: dict(state))
    doc = json.loads(outcome.evidence.read_text(encoding="utf-8"))
    assert doc["before"] == {"outbox": 0} and doc["after"] == {"outbox": 1}
    assert doc["answer"] == {"status": 202, "body": {"queued": True}}
    assert attached and attached[0][0] == "evidence" and attached[0][1]["kind"] == "other"


def test_refused_calls_write_no_evidence(tmp_path):
    g = guard(tmp_path)
    g.call("SUB-000001", fresh, ok)
    assert list(tmp_path.iterdir()) == []


def test_fixture_runs_the_effect_once_for_the_whole_module(pytester, tmp_path):
    counter = tmp_path / "sent.txt"
    pytester.makepyfile(
        test_probe=textwrap.dedent(
            f"""
            from pathlib import Path
            from gate.side_effect import SideEffectGuard, side_effect_fixture

            COUNTER = Path({str(counter)!r})

            def send(record_id):
                COUNTER.write_text(COUNTER.read_text() + "x" if COUNTER.exists() else "x")
                return 202, {{"id": record_id}}

            sent = side_effect_fixture(
                lambda: SideEffectGuard("SEND_PROBE", {{"qa"}}, 1, 1,
                                        environ={{"SEND_PROBE": "1"}},
                                        evidence_dir=Path({str(tmp_path)!r})),
                lambda g: g.call("SUB-000123", lambda _b: None, send),
                env=lambda _request: "qa",
            )

            def test_status(sent):
                assert sent.outcome.status == 202

            def test_body(sent):
                assert sent.outcome.answer == {{"id": "SUB-000123"}}

            def test_same_run(sent):
                assert sent.guard.sent == 1
            """
        )
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-p", "no:randomly")
    result.assert_outcomes(passed=3)
    assert counter.read_text() == "x"


def test_fixture_skips_every_reader_when_not_opted_in(pytester):
    pytester.makepyfile(
        test_probe=textwrap.dedent(
            """
            from gate.side_effect import SideEffectGuard, side_effect_fixture

            sent = side_effect_fixture(
                lambda: SideEffectGuard("SEND_PROBE", {"qa"}, 1, 1, environ={}),
                lambda g: g.call("SUB-000123", lambda _b: None, lambda _i: (200, None)),
                env=lambda _request: "qa",
            )

            def test_a(sent): pass
            def test_b(sent): pass
            """
        )
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-rs")
    result.assert_outcomes(skipped=2)
    result.stdout.fnmatch_lines(["*opt-in*"])
