"""pytest --junitxml, imported, gives the outcomes the recorder stored for the same run."""

from __future__ import annotations

from results.io.junit import import_junit
from results.store import open_store
from tests.results.conftest import PLUGIN

pytest_plugins = ["pytester"]

SUITE = """
import pytest

def test_pass():
    pass

def test_fail():
    assert 1 == 2

@pytest.mark.skip(reason="later")
def test_skip():
    pass

@pytest.mark.xfail(reason="known")
def test_xfail():
    assert False

class TestGroup:
    @pytest.mark.parametrize("n", [1, 2])
    def test_param(self, n):
        pass

@pytest.fixture
def broken():
    raise RuntimeError("setup broke")

def test_error(broken):
    pass
"""


def test_round_trip_equals_the_recorder(pytester, db_path, tmp_path):
    pytester.makepyfile(test_rt=SUITE)
    xml = pytester.path / "junit.xml"
    pytester.runpytest(*PLUGIN, f"--junitxml={xml}")
    recorded = open_store(str(db_path))
    run_id = recorded.query("runs_list")[0]["run_id"]
    expected = {
        r["nodeid"]: r["outcome"] for r in recorded.query("run_final_results", run_id=run_id)
    }
    recorded.close()

    imported = open_store(str(tmp_path / "imported.db"))
    import_junit(imported, xml, framework="pytest", run_id="from-xml")
    got = {
        r["nodeid"]: r["outcome"] for r in imported.query("run_final_results", run_id="from-xml")
    }
    imported.close()
    assert got == expected
    assert set(expected.values()) == {"passed", "failed", "skipped", "xfailed", "error"}
