"""A case_id pin survives a rename; without one a rename is removed plus added."""

from __future__ import annotations

from results.catalogue import build_catalogue, diff_catalogues

pytest_plugins = ["pytester"]


def test_pinned_identity_survives_a_rename(pytester):
    pytester.makepyfile(
        test_p='import pytest\n\n@pytest.mark.case_id("TC-0042")\ndef test_old():\n    pass\n\n'
        "def test_free():\n    pass\n"
    )
    before = build_catalogue(pytester.path)
    pytester.makepyfile(
        test_p='import pytest\n\n@pytest.mark.case_id("TC-0042")\ndef test_new():\n    pass\n\n'
        "def test_free_renamed():\n    pass\n"
    )
    after = build_catalogue(pytester.path)
    diff = diff_catalogues(before, after)
    assert [(o.case_key, n.case_key) for o, n in diff.renamed] == [
        ("test_p.py::test_old", "test_p.py::test_new")
    ]
    assert [c.case_key for c in diff.added] == ["test_p.py::test_free_renamed"]
    assert [c.case_key for c in diff.removed] == ["test_p.py::test_free"]
    md = diff.to_markdown()
    assert "Renamed" in md and "test_free_renamed" in md
    assert diff.to_json()["renamed"][0]["case_id"] == "TC-0042"


def test_identical_catalogues_have_an_empty_diff(pytester):
    pytester.makepyfile(test_q="def test_a():\n    pass\n")
    cat = build_catalogue(pytester.path)
    assert diff_catalogues(cat, cat).empty
    assert diff_catalogues(cat, cat).to_markdown() == "No test case changes.\n"


def test_changed_fields_are_named(pytester):
    pytester.makepyfile(test_r='def test_a():\n    """Old title."""\n')
    before = build_catalogue(pytester.path)
    pytester.makepyfile(test_r='def test_a():\n    """New title."""\n')
    diff = diff_catalogues(before, build_catalogue(pytester.path))
    assert [f for _, _, f in diff.changed] == [["title", "current_revision_id"]]
