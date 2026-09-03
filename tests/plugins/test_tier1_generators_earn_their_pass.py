"""
The tier-1 generators must not report health they never established.

``unit_ai`` and ``integration_graph`` both ended in an unconditional
``status="pass"``. Neither could return anything else, so an LLM outage, an
empty source tree, an unparseable file and a service graph with no edges all
scored identically to a clean run — and ``unit_ai`` wrote the empty string
``complete()`` returns on failure to disk as a test file while doing it.

The positive controls in each class are load-bearing. A generator hardwired to
``unknown`` satisfies every failure test below on its own, which is the same
false verdict the status vocabulary exists to prevent, pointed the other way.
"""

from __future__ import annotations

import pytest

from api.llm.base import AUTH, RATE_LIMITED, Completion
from plugins.cost_governor import CostGovernor
from tests.plugins._tier1 import FakeLLM, load, write_module

GENERATED = "import pytest\n\n\ndef test_generated():\n    assert True\n"


@pytest.fixture
def unit_ai():
    return load("unit_ai").UnitAIPlugin()


@pytest.fixture
def integration_graph():
    return load("integration_graph").IntegrationGraphPlugin()


@pytest.fixture
def source_tree(tmp_path):
    """An empty source directory plus a separate output directory."""
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    return src, out


def _context(src, out, **extra) -> dict:
    return {"source_dir": str(src), "out_dir": str(out), **extra}


class TestUnitAiAnOutageIsNotAPass:
    """
    Probe of the unfixed plugin: ``STATUS: pass, FINDINGS: [{'generated_files':
    [...], 'count': 1}], FILE bytes=0``. ``complete()`` is documented as
    returning ``""`` on failure, so the ``except Exception`` guarding the call
    never fired for a 401, a 429, a 5xx or an unparseable 200.
    """

    def test_an_auth_failure_is_unknown(self, unit_ai, source_tree, monkeypatch):
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")
        FakeLLM(Completion.failed(AUTH, "HTTP 401")).install(monkeypatch)

        result = unit_ai.run(_context(src, out))

        assert result.status == "unknown"
        assert "auth" in str(result.findings)

    def test_an_outage_writes_no_test_file(self, unit_ai, source_tree, monkeypatch):
        """The zero-byte file was counted as generated and left on disk."""
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")
        FakeLLM(Completion.failed(RATE_LIMITED, "HTTP 429")).install(monkeypatch)

        result = unit_ai.run(_context(src, out))

        assert list(out.glob("*.py")) == []
        assert result.findings[0]["count"] == 0, "an outage was counted as a generated test"

    def test_a_two_hundred_with_no_text_is_also_unknown(self, unit_ai, source_tree, monkeypatch):
        """`ok` with an empty body is the other route to a zero-byte file."""
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")
        FakeLLM(Completion(text="   ")).install(monkeypatch)

        result = unit_ai.run(_context(src, out))

        assert result.status == "unknown"
        assert list(out.glob("*.py")) == []

    def test_a_partial_outage_still_reports_what_it_generated(
        self, unit_ai, source_tree, monkeypatch
    ):
        """Some tests really were written, so this is `warn`, not `unknown`."""
        src, out = source_tree
        write_module(src, "a_mod.py", "def f():\n    return 1\n")
        write_module(src, "b_mod.py", "def g():\n    return 2\n")
        FakeLLM(Completion(text=GENERATED), Completion.failed(RATE_LIMITED, "HTTP 429")).install(
            monkeypatch
        )

        result = unit_ai.run(_context(src, out))

        assert result.status == "warn"
        assert result.findings[0]["count"] == 1
        assert len(result.findings[0]["llm_outages"]) == 1


class TestUnitAiAbsenceOfInputIsNotHealth:
    def test_an_empty_source_dir_is_unknown(self, unit_ai, source_tree, monkeypatch):
        src, out = source_tree
        FakeLLM().install(monkeypatch)

        result = unit_ai.run(_context(src, out))

        assert result.status == "unknown"

    def test_a_missing_source_dir_is_unknown(self, unit_ai, tmp_path, monkeypatch):
        FakeLLM().install(monkeypatch)

        result = unit_ai.run(_context(tmp_path / "nope", tmp_path / "out"))

        assert result.status == "unknown"

    def test_a_tree_of_unparseable_sources_is_unknown(self, unit_ai, source_tree, monkeypatch):
        """`ast.parse` failing used to `continue` straight through to `pass`."""
        src, out = source_tree
        write_module(src, "broken.py", "def f(:\n")
        FakeLLM().install(monkeypatch)

        result = unit_ai.run(_context(src, out))

        assert result.status == "unknown"
        assert len(result.findings[0]["unparseable"]) == 1

    def test_a_virtualenv_is_not_read(self, unit_ai, source_tree, monkeypatch):
        """3,353 of this checkout's 3,574 `*.py` are site-packages."""
        src, out = source_tree
        write_module(src, "mine.py", "def f():\n    return 1\n")
        write_module(src / ".venv" / "lib" / "site-packages" / "dep", "dep.py", "x = 1\n")
        FakeLLM(Completion(text=GENERATED)).install(monkeypatch)

        result = unit_ai.run(_context(src, out))

        assert result.findings[0]["candidates"] == 1

    def test_the_file_cap_is_stated_rather_than_silent(self, unit_ai, source_tree, monkeypatch):
        """It read five files and reported on the whole directory."""
        src, out = source_tree
        for i in range(7):
            write_module(src, f"mod_{i}.py", f"def f{i}():\n    return {i}\n")
        FakeLLM(Completion(text=GENERATED)).install(monkeypatch)

        result = unit_ai.run(_context(src, out, max_files=2))

        assert result.findings[0]["candidates"] == 7
        assert result.findings[0]["examined"] == 2
        assert result.findings[0]["not_examined"] == 5


class TestUnitAiBooksOnlyWhatItSpent:
    def test_an_outage_costs_nothing(self, unit_ai, source_tree, monkeypatch):
        """`total_cost += 0.0001` was booked per file, call or no call."""
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")
        FakeLLM(Completion.failed(AUTH, "HTTP 401")).install(monkeypatch)
        governor = CostGovernor()

        result = unit_ai.run(_context(src, out, cost_governor=governor))

        assert result.cost_usd == 0.0
        assert governor.budget_used == 0.0

    def test_cost_scales_with_what_was_returned(self, unit_ai, source_tree, monkeypatch):
        """
        Flat pricing meant the governor's budget never moved: five files against
        the $5 default is 0.01%, so the 20%-remaining downgrade was dead code.
        """
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")

        FakeLLM(Completion(text=GENERATED)).install(monkeypatch)
        small = unit_ai.run(_context(src, out))

        FakeLLM(Completion(text=GENERATED * 500)).install(monkeypatch)
        large = unit_ai.run(_context(src, out))

        assert small.cost_usd > 0.0, "a call that happened must be booked"
        assert large.cost_usd > small.cost_usd * 10


class TestUnitAiStillGeneratesTests:
    """Positive control: a plugin answering `unknown` to everything would pass
    every test above while being useless."""

    def test_a_real_completion_is_a_pass(self, unit_ai, source_tree, monkeypatch):
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")
        FakeLLM(Completion(text=GENERATED)).install(monkeypatch)

        result = unit_ai.run(_context(src, out))

        assert result.status == "pass"
        assert result.findings[0]["count"] == 1
        assert (out / "test_mod_ai.py").read_text() == GENERATED

    def test_the_model_was_actually_asked(self, unit_ai, source_tree, monkeypatch):
        """Without this, "unknown" could be right for the wrong reason."""
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")
        fake = FakeLLM(Completion(text=GENERATED)).install(monkeypatch)

        unit_ai.run(_context(src, out))

        assert len(fake.prompts) == 1
        assert "def f()" in fake.prompts[0]


class TestIntegrationGraphAnEmptyMapIsNotHealth:
    def test_a_missing_source_dir_is_unknown(self, integration_graph, tmp_path):
        result = integration_graph.run(_context(tmp_path / "nope", tmp_path / "out"))

        assert result.status == "unknown"

    def test_a_tree_with_no_python_is_unknown(self, integration_graph, source_tree):
        src, out = source_tree

        result = integration_graph.run(_context(src, out))

        assert result.status == "unknown"

    def test_finding_no_endpoints_is_unknown(self, integration_graph, source_tree):
        """It scanned, it parsed, it verified no dependency. That is not a pass."""
        src, out = source_tree
        write_module(src, "mod.py", "def f():\n    return 1\n")

        result = integration_graph.run(_context(src, out))

        assert result.status == "unknown"
        assert result.findings[0]["reason"]

    def test_a_virtualenv_is_not_walked(self, integration_graph, source_tree):
        """`rglob` over a checkout with a venv parses 3,353 files of dependency
        code, measured at 25.9s, then `execute` retries it three times."""
        src, out = source_tree
        write_module(src, "mine.py", 'import requests\nrequests.get("https://svc.test/health")\n')
        write_module(
            src / ".venv" / "lib" / "site-packages" / "dep",
            "dep.py",
            'import requests\nrequests.get("https://dependency.test/x")\n',
        )

        result = integration_graph.run(_context(src, out))

        assert result.findings[0]["endpoints"] == 1
        assert result.findings[0]["files_scanned"] == 1


class TestIntegrationGraphCanReportAProblem:
    def test_a_url_with_no_scheme_is_a_failure(self, integration_graph, source_tree):
        """`requests.get("api/v1/users")` raises MissingSchema before it opens a
        socket — a defect in the source, not a service being down."""
        src, out = source_tree
        write_module(src, "mod.py", 'import requests\nrequests.get("api/v1/users")\n')

        result = integration_graph.run(_context(src, out))

        assert result.status == "fail"
        assert result.findings[0]["unroutable_endpoints"][0]["url"] == "api/v1/users"

    def test_a_post_endpoint_is_not_handed_a_get_probe(self, integration_graph, source_tree):
        """
        The one endpoint this finds against the real repository is
        `https://api.anthropic.com/v1/messages` — POST-only and authenticated —
        and it generated `requests.get(...)` + `assert status_code == 200`, a
        test that can only fail, then reported `pass`.
        """
        src, out = source_tree
        write_module(src, "mod.py", 'import requests\nrequests.post("https://svc.test/orders")\n')

        result = integration_graph.run(_context(src, out))

        assert result.status == "warn"
        assert result.findings[0]["generated_files"] == []
        assert result.findings[0]["not_probeable_endpoints"][0]["method"] == "post"

    def test_a_dry_run_verifies_nothing_and_says_so(self, integration_graph, source_tree):
        src, out = source_tree
        write_module(src, "mod.py", 'import requests\nrequests.get("https://svc.test/health")\n')

        result = integration_graph.run(_context(src, out, dry_run=True))

        assert result.status == "skip"


class TestIntegrationGraphStillMapsAndGenerates:
    """Positive control for the class above."""

    def test_a_get_endpoint_passes_and_produces_a_probe(self, integration_graph, source_tree):
        src, out = source_tree
        write_module(src, "mod.py", 'import requests\nrequests.get("https://svc.test/health")\n')

        result = integration_graph.run(_context(src, out))

        assert result.status == "pass"
        assert result.findings[0]["endpoints"] == 1
        written = list(out.glob("test_integration_*.py"))
        assert len(written) == 1
        assert "https://svc.test/health" in written[0].read_text()

    def test_the_dependency_map_still_names_the_caller(self, integration_graph, source_tree):
        src, out = source_tree
        module = write_module(
            src, "mod.py", 'import requests\nrequests.get("https://svc.test/health")\n'
        )

        result = integration_graph.run(_context(src, out))

        assert result.findings[0]["dependency_map"][str(module)] == ["https://svc.test/health"]
