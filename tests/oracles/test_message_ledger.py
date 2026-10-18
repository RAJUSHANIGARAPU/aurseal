"""Stored-once rule: before/after overviews around one hand-off."""

from __future__ import annotations

from oracles.message_ledger import added, stored_once

OLD = {
    "id": "m1",
    "type": "BOOKING",
    "status": "SENT",
    "recipient": "R-1",
    "created_at": "2026-03-01T00:00:00.000Z",
}
NEW = {
    "id": "m2",
    "type": "CHANGE",
    "status": "NEW",
    "recipient": "R-1",
    "created_at": "2026-03-10T09:00:00.000Z",
}


def check(before, after, ok=True, **kw):
    return stored_once(before, after, handoff_succeeded=ok, expected_type="CHANGE", **kw)


def test_one_new_message_holds():
    assert check([OLD], [NEW, OLD]) == []
    assert added([OLD], [NEW, OLD]) == [NEW]


def test_double_store_is_two_new_messages():
    twin = {**NEW, "id": "m3"}
    (problem,) = check([OLD], [twin, NEW, OLD])
    assert problem.startswith("expected exactly one new message, found 2")


def test_duplicate_id_and_lost_message():
    problems = check([OLD], [NEW, NEW])
    assert "duplicate message id(s): ['m2']" in problems
    assert "message(s) seen before the hand-off are gone: ['m1']" in problems


def test_failed_handoff_must_store_nothing():
    assert check([OLD], [OLD], ok=False) == []
    assert check([OLD], [NEW, OLD], ok=False) == [
        "the hand-off failed but 1 message(s) were stored: ['m2']"
    ]


def test_wrong_type_status_recipient_and_time():
    bad = {
        **NEW,
        "type": "BOOKING",
        "status": "SENT",
        "recipient": "R-2",
        "created_at": "2026-02-01",
    }
    problems = check([OLD], [bad, OLD])
    assert problems == [
        "new message has type 'BOOKING', expected 'CHANGE'",
        "new message has status 'SENT', expected one of ['NEW', 'TO_BE_SENT']",
        "new message goes to 'R-2', expected 'R-1'",
        "new message created 2026-02-01, before the latest earlier one 2026-03-01T00:00:00.000Z",
    ]


def test_first_message_and_explicit_recipient():
    assert check([], [NEW]) == []
    assert check([], [NEW], recipient="R-9") == ["new message goes to 'R-1', expected 'R-9'"]
