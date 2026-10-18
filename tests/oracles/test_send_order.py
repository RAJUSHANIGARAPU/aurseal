"""Send-order hypotheses: which declared ordering an observed send fits."""

from __future__ import annotations

from oracles.send_order import (
    BY_TYPE,
    TYPE_THEN_ID,
    TYPE_THEN_NAME,
    FileState,
    describe,
    files_from,
    fitting_hypotheses,
    identify,
    orderable,
    violations,
)

BUILDS = {"build-1": TYPE_THEN_ID, "build-2": TYPE_THEN_NAME}
HYPOTHESES = [BY_TYPE, TYPE_THEN_NAME, TYPE_THEN_ID]


def f(type_, id_, name, sent_at, state="SENT"):
    return FileState(type_, id_, name, state, sent_at)


# ids and names disagree for the two attachments: id 3 is "b", id 4 is "a"
BY_ID = [
    f("ATTACHMENT", "3", "b", "t1"),
    f("ATTACHMENT", "4", "a", "t2"),
    f("SEGMENTS", "1", "x", "t3"),
    f("XML", "2", "y", "t4"),
]
BY_NAME = [
    f("ATTACHMENT", "4", "a", "t1"),
    f("ATTACHMENT", "3", "b", "t2"),
    f("SEGMENTS", "1", "x", "t3"),
    f("XML", "2", "y", "t4"),
]


def test_each_send_fits_its_own_ordering_and_identifies_the_build():
    assert fitting_hypotheses(BY_ID, HYPOTHESES) == ["type", "type+id"]
    assert fitting_hypotheses(BY_NAME, HYPOTHESES) == ["type", "type+name"]
    assert identify(BY_ID, BUILDS) == "build-1"
    assert identify(BY_NAME, BUILDS) == "build-2"


def test_violation_quotes_every_file():
    problems = violations(BY_NAME, TYPE_THEN_ID)
    assert problems[0] == "ATTACHMENT/b was sent after a file ordered behind it"
    assert problems[-1].startswith("files under 'type+id': ATTACHMENT/b/3=SENT@t2")
    assert describe(BY_NAME[:1]) == "ATTACHMENT/a/4=SENT@t1"


def test_prefix_rule_a_later_file_sent_while_an_earlier_waits():
    waiting = [
        f("SEGMENTS", "1", "x", None, "READY"),
        f("XML", "2", "y", "t1"),
        f("XML", "5", "z", "t2"),
    ]
    assert (
        violations(waiting, BY_TYPE)[0] == "XML/y was sent while a file before it is still waiting"
    )


def test_single_file_resend_is_not_evidence():
    resend = [f("SEGMENTS", "1", "x", "t9"), f("XML", "2", "y", None, "READY")]
    assert not orderable(resend)
    assert fitting_hypotheses(resend, HYPOTHESES) == []
    assert identify(resend, BUILDS) is None


def test_ambiguous_when_no_tie_to_break():
    plain = BY_ID[2:]
    assert fitting_hypotheses(plain, HYPOTHESES) == ["type", "type+name", "type+id"]
    assert identify(plain, BUILDS) is None


def test_files_from_details_body_and_numeric_ids():
    details = {
        "files": [{"type": "XML", "file_id": 10, "name": "n", "state": "SENT", "sent_at": "t"}]
    }
    assert files_from(details) == [FileState("XML", "10", "n", "SENT", "t")]
    numeric = [f("A", "9", "x", "t1"), f("A", "10", "y", "t2")]  # 9 < 10 as numbers
    assert violations(numeric, TYPE_THEN_ID) == []
