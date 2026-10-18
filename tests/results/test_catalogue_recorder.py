"""The recorder stamps every result row with its catalogue case, under xdist too."""

from __future__ import annotations

from results.api import dispatch
from tests.results._helpers import rows
from tests.results.conftest import PLUGIN

pytest_plugins = ["pytester"]

SAMPLE = '''
import pytest

@pytest.mark.parametrize("n", range(4))
@pytest.mark.requirement("REQ-7")
def test_rows(n):
    """Every row works."""

@pytest.mark.case_id("TC-9")
def test_pinned():
    pass
'''


def test_rows_point_at_cases_under_xdist(pytester, db_path, subprocess_env, store):
    pytester.makepyfile(test_cat=SAMPLE)
    pytester.runpytest_subprocess(*PLUGIN, "-n", "2")
    results = rows(db_path, "SELECT nodeid, case_id FROM test_results")
    assert len(results) == 5 and all(r["case_id"] for r in results)
    cases = rows(db_path, "SELECT case_id, case_key, title FROM cases ORDER BY case_key")
    assert [c["case_key"] for c in cases] == ["test_cat.py::test_pinned", "test_cat.py::test_rows"]
    assert {c["case_id"] for c in cases} >= {"TC-9"}
    links = rows(db_path, "SELECT kind, ref FROM case_links")
    assert links == [{"kind": "requirement", "ref": "REQ-7"}]

    listed = dispatch(store, "GET", "/cases", params={"requirement": "REQ-7"})
    assert [c["title"] for c in listed["cases"]] == ["Every row works."]
    one = dispatch(store, "GET", "/cases/TC-9")
    assert one["case_key"] == "test_cat.py::test_pinned"
    assert dispatch(store, "GET", "/catalogue")["case_count"] == 2
