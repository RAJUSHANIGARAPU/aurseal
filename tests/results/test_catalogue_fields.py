"""Catalogue fields and their precedence, collected from a sample project (never run)."""

from __future__ import annotations

from results.catalogue import (
    build_catalogue,
    case_id_for_key,
    case_id_for_nodeid,
    case_key_from_nodeid,
    codeowner,
)

pytest_plugins = ["pytester"]

SAMPLE = '''
import allure
import pytest


@allure.title("Checkout totals add up")
@allure.severity("critical")
def test_allure_title():
    """Docstring title loses to the allure title.

    Totals include tax and shipping.
    """


@pytest.mark.owner("team-pay")
@pytest.mark.priority("P1")
@pytest.mark.requirement("REQ-12")
@pytest.mark.feature("billing")
@pytest.mark.smoke
def test_markers_win():
    """Paying an invoice marks it paid.

    Steps:
    1. Create an invoice
    2. Pay it
    3. Read it back

    Anything after the block is description.
    """


@pytest.mark.parametrize("n", [1, 2, 3])
def test_humanised_name(n):
    pass


class TestGroup:
    @pytest.mark.known_defect("BUG-142")
    def test_in_class(self):
        """Grouped."""
'''


def _cases(pytester):
    pytester.makepyfile(test_sample=SAMPLE)
    (pytester.path / "CODEOWNERS").write_text("*.py @team-default\n/test_sample.py @team-sample\n")
    cat = build_catalogue(pytester.path)
    assert not cat.errors, cat.errors
    return {c.qualname: c for c in cat.cases}


def test_fields_and_precedence(pytester):
    cases = _cases(pytester)
    assert set(cases) == {
        "test_allure_title",
        "test_markers_win",
        "test_humanised_name",
        "TestGroup.test_in_class",
    }

    allure_case = cases["test_allure_title"]
    assert allure_case.title == "Checkout totals add up"
    assert allure_case.description == "Totals include tax and shipping."
    assert allure_case.priority == "critical"  # allure severity when no priority marker
    assert allure_case.owner == "@team-sample"  # CODEOWNERS: the last matching rule wins

    marked = cases["test_markers_win"]
    assert marked.title == "Paying an invoice marks it paid."
    assert marked.owner == "team-pay" and marked.priority == "P1"
    assert marked.steps == ("Create an invoice", "Pay it", "Read it back")
    assert {"name": "requirement", "args": ["REQ-12"]} in marked.markers
    assert {"name": "smoke", "args": []} in marked.markers
    assert set(marked.links) == {("requirement", "REQ-12"), ("feature", "billing")}

    param = cases["test_humanised_name"]
    assert param.title == "Humanised name"
    assert param.case_key == "test_sample.py::test_humanised_name"  # one case, three params

    grouped = cases["TestGroup.test_in_class"]
    assert grouped.case_key == "test_sample.py::TestGroup.test_in_class"
    assert ("defect", "BUG-142") in grouped.links
    assert grouped.file_path == "test_sample.py" and grouped.line and grouped.line > 1


def test_case_key_and_id_derive_from_a_nodeid():
    assert case_key_from_nodeid("tests/x.py::TestA::test_b[1-2]") == "tests/x.py::TestA.test_b"
    assert case_key_from_nodeid("tests/x.py::test_c") == "tests/x.py::test_c"
    assert case_id_for_nodeid("tests/x.py::test_c[a]") == case_id_for_key("tests/x.py::test_c")
    assert case_id_for_key("k").startswith("c-") and len(case_id_for_key("k")) == 18


def test_codeowners_patterns(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        "# comment\n*.py @all\ntests/api/ @api-team\ndocs/** @docs\n"
    )
    assert codeowner(tmp_path, "tests/api/test_x.py") == "@api-team"
    assert codeowner(tmp_path, "tests/ui/test_y.py") == "@all"
    assert codeowner(tmp_path, "README.md") is None
