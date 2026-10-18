"""Open-issue manifests: loading, "an open issue never makes a check pass", and the rails."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verification.known_defects_manifest import (
    ManifestEntry,
    OpenIssue,
    by_template,
    check_manifest,
    detector_split,
    judge_findings,
    load_manifest,
    parse_manifest,
    tracked_defect_verdicts,
)
from verification.verdicts import RedVerdict, read_verdicts

EMITTED = {"SUM-DRIFT", "EMPTY-CELL", "BROKEN-TOKEN"}

DRIFT = OpenIssue("ISS-310", "sum drift", "total one cent high", "sample 3, row 2", "SUM-DRIFT")
FOOTER = OpenIssue("ISS-311", "wording", "old footer", "page 2")
TOKEN = OpenIssue("ISS-312", "template", "broken token", "row 9", "BROKEN-TOKEN")


def _write(directory: Path, name: str, entries: list[dict]) -> Path:
    path = directory / name
    path.write_text(json.dumps(entries))
    return path


def test_load_finds_every_manifest_file_and_sorts(tmp_path):
    _write(tmp_path, "letters.manifest.json", [{"template": "zeta"}])
    _write(
        tmp_path,
        "billing.manifest.json",
        [
            {
                "template": "Alpha",
                "absent_from_envs": ["staging"],
                "open_issues": [
                    {"issue": "ISS-310", "summary": "sum drift", "finding": "SUM-DRIFT"},
                    {"issue": "ISS-311", "summary": "wording"},
                ],
            }
        ],
    )
    _write(tmp_path, "unrelated.json", [{"template": "ignored"}])
    entries = load_manifest(tmp_path)
    assert [e.template for e in entries] == ["Alpha", "zeta"]
    alpha = by_template(entries, "Alpha")
    assert alpha is not None and alpha.absent_from_envs == ("staging",)
    assert [d.is_detector for d in alpha.open_issues] == [True, False]
    assert by_template(entries, "missing") is None


def test_keys_of_another_layout_are_not_read(tmp_path):
    with pytest.raises(ValueError, match="no 'template'"):
        parse_manifest([{"name": "x", "open_issues": []}])
    entry = parse_manifest([{"template": "x", "issues": [{"issue": "ISS-1"}]}])[0]
    assert entry.open_issues == ()


def test_load_rejects_an_empty_directory_and_bad_shapes(tmp_path):
    with pytest.raises(ValueError, match="nothing to check"):
        load_manifest(tmp_path)
    with pytest.raises(ValueError, match="JSON list"):
        parse_manifest({"template": "x"})
    with pytest.raises(ValueError, match="object"):
        parse_manifest(["x"])


def test_note_quotes_every_issue_and_marks_notes_as_not_asserted():
    entry = ManifestEntry("statement", open_issues=(DRIFT, FOOTER))
    note = entry.open_issue_note()
    assert "ISS-310" in note and "asserted as SUM-DRIFT" in note
    assert "ISS-311" in note and "note, not asserted" in note
    assert ManifestEntry("clean").open_issue_note() == ""


def test_only_observable_detectors_get_a_verdict():
    verdicts = tracked_defect_verdicts(
        "statement",
        [DRIFT, FOOTER, TOKEN],
        observed_kinds={"SUM-DRIFT"},
        observable_kinds={"SUM-DRIFT", "EMPTY-CELL"},
    )
    # TOKEN is out of this observer's reach: silence, not an invented "cleared".
    assert [(v.ticket, v.verdict) for v in verdicts] == [("ISS-310", RedVerdict.REPRODUCES)]
    cleared = tracked_defect_verdicts("statement", [DRIFT], set(), {"SUM-DRIFT"})
    assert cleared[0].verdict is RedVerdict.CLEARED


def test_an_open_issue_never_makes_the_check_pass():
    entry = ManifestEntry("statement", open_issues=(DRIFT, FOOTER))
    # A note excuses nothing: its finding stays unexplained.
    judgement = judge_findings(entry, ["SUM-DRIFT", "EMPTY-CELL"], EMITTED)
    assert judgement.unexplained == ("EMPTY-CELL",)
    with pytest.raises(AssertionError) as caught:
        judgement.assert_clean()
    assert "EMPTY-CELL" in str(caught.value) and "ISS-311" in str(caught.value)


def test_a_detector_excuses_its_finding_and_reports_when_it_clears():
    entry = ManifestEntry("statement", open_issues=(DRIFT,))
    judge_findings(entry, ["SUM-DRIFT"], EMITTED).assert_clean()
    gone = judge_findings(entry, [], EMITTED)
    with pytest.raises(AssertionError, match="KNOWN DEFECT CLEARED: ISS-310"):
        gone.assert_clean()
    assert [(r["ticket"], r["verdict"]) for r in read_verdicts()] == [
        ("ISS-310", "reproduces"),
        ("ISS-310", "cleared"),
    ]


def test_judging_without_recording_writes_nothing():
    judge_findings(ManifestEntry("s", open_issues=(DRIFT,)), [], EMITTED, record=False)
    assert read_verdicts() == []


def _entry(template: str, *issues: OpenIssue, **data) -> ManifestEntry:
    return ManifestEntry(template, open_issues=issues, data={"template": template, **data})


def test_a_sound_manifest_has_no_problems(tmp_path):
    (tmp_path / "sample.json").write_text("{}")
    entries = [
        _entry("a", DRIFT, FOOTER, sums=["Total"]),
        _entry("b", FOOTER),
        _entry("c", OpenIssue("ISS-313", "layout", "wide column", "page 1")),
    ]
    entries.append(
        ManifestEntry("d", samples=("sample.json",), source=tmp_path / "x.manifest.json")
    )
    problems = check_manifest(
        entries,
        emitted_kinds=EMITTED,
        known_envs={"qa", "staging"},
        oracle_inputs={"SUM-DRIFT": "sums"},
    )
    assert problems == []
    assert detector_split(entries) == (1, 3)


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([_entry("a"), _entry("a")], "has entries in both"),
        ([_entry("a", OpenIssue("", "sum drift"), FOOTER, FOOTER)], "needs an issue id"),
        (
            [_entry("a", OpenIssue("ISS-1", "k", "d", "", "SUM-DRIFT"), FOOTER, FOOTER)],
            "does not say where it was seen",
        ),
        (
            [_entry("a", OpenIssue("ISS-1", "k", "d", "e", "NOBODY-EMITS"), FOOTER, FOOTER)],
            "which no check emits",
        ),
        ([_entry("a", DRIFT, FOOTER, FOOTER, sums=[])], "can never fire"),
        ([_entry("a", DRIFT, TOKEN, FOOTER, sums=["T"])], "excused from the main checks"),
    ],
)
def test_each_gate_safety_rail_fires(entries, expected):
    problems = check_manifest(entries, emitted_kinds=EMITTED, oracle_inputs={"SUM-DRIFT": "sums"})
    assert any(expected in p for p in problems), problems


def test_unknown_environment_and_broken_samples_are_reported(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    entry = ManifestEntry(
        "a",
        absent_from_envs=("stagign",),
        samples=("missing.json", "bad.json"),
        source=tmp_path / "a.manifest.json",
    )
    problems = check_manifest([entry], known_envs={"qa", "staging"})
    assert any("'stagign' is not a known environment" in p for p in problems)
    assert any("missing.json does not exist" in p for p in problems)
    assert any("bad.json is not valid JSON" in p for p in problems)
