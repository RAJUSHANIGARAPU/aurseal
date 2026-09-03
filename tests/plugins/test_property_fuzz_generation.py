"""What ``property-fuzz`` counted and what it left on disk were different numbers.

Three defects, all of which the plugin reported ``pass`` through:

* the write was inside the per-function loop but the filename came from the
  module, so three functions in one file meant three writes to one path — the
  last survived, the other two were counted and lost;
* a model hint was accepted on ``"st." in hint``, so ordinary prose was spliced
  into Python source and a ``SyntaxError`` was written out as a generated test;
* ``module_import`` was built from the absolute path, so every generated test
  fell into its own ``except`` clause and passed having imported nothing.

``TestItStillGenerates`` is the control set. Every assertion here is satisfied
by a plugin that generates nothing and answers ``unknown``, which would be the
same failure pointed the other way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.plugins._tier3_support import StubGovernor, load_module, load_plugin, write_source_tree

_OUT_DIR = Path("ai_generated_tests/property")

_THREE_FUNCTIONS = """
def alpha(a):
    return a


def beta(b):
    return b


def gamma(c):
    return c
"""

_ONE_FUNCTION = "def scale(x):\n    return x\n"
_VENDORED = "def vendored(q):\n    return q\n"


@pytest.fixture
def plugin():
    return load_plugin("property_fuzz.plugin.py", "PropertyFuzzPlugin")


@pytest.fixture
def source_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tree = {"pkg/__init__.py": "", "pkg/calc.py": _ONE_FUNCTION}
    return write_source_tree(tmp_path / "src", tree)


def _run(plugin, source_dir, governor, **overrides):
    return plugin.run({"source_dir": str(source_dir), "cost_governor": governor, **overrides})


def _written() -> list[Path]:
    return sorted(_OUT_DIR.glob("*.py")) if _OUT_DIR.exists() else []


class TestTheCountMatchesWhatIsOnDisk:
    def test_one_file_per_module_not_one_per_function(self, plugin, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        root = write_source_tree(tmp_path / "src", {"pkg/calc.py": _THREE_FUNCTIONS})
        result = _run(plugin, root, StubGovernor(""))

        generated = result.findings[0]["generated_files"]
        assert len(generated) == len(set(generated)), "the same path was counted more than once"
        assert len(generated) == len(_written())

    def test_no_function_is_silently_dropped(self, plugin, tmp_path, monkeypatch):
        """Three functions were reported and one test survived the overwrite."""
        monkeypatch.chdir(tmp_path)
        root = write_source_tree(tmp_path / "src", {"pkg/calc.py": _THREE_FUNCTIONS})
        _run(plugin, root, StubGovernor(""))

        content = _written()[0].read_text(encoding="utf-8")
        for name in ("alpha", "beta", "gamma"):
            assert f"def test_{name}_properties(" in content


class TestModelProseIsNotSplicedIntoSource:
    @pytest.mark.parametrize(
        "hint",
        [
            "Use st.integers() for x.",
            "x=st.integers(min_value=0",
            "I'd suggest st.text() here",
            "y=st.text()",
            "x=os.system('id')",
            "x=st.integers(), z=st.text()",
        ],
    )
    def test_an_unusable_hint_never_reaches_the_file(self, plugin, source_dir, hint):
        result = _run(plugin, source_dir, StubGovernor(hint))
        assert result.status == "pass"
        content = _written()[0].read_text(encoding="utf-8")
        ast.parse(content)  # raises SyntaxError if the hint was spliced in raw
        assert "st.one_of(st.text(), st.integers(), st.none())" in content

    def test_source_that_does_not_parse_is_never_written(self, plugin, source_dir, monkeypatch):
        """Belt and braces for the check above: if assembly produces a broken
        module for any reason, it must not land on top of a working one."""
        module = load_module("property_fuzz.plugin.py")
        monkeypatch.setattr(module, "_TEST_TEMPLATE", "def test_{func_name}( :\n")
        broken = module.PropertyFuzzPlugin()
        result = _run(broken, source_dir, StubGovernor(""))

        assert result.status == "unknown"
        assert _written() == []
        assert "did not parse" in str(result.findings)


class TestTheGeneratedTestCanActuallyImport:
    def test_the_import_is_a_relative_dotted_path(self, plugin, source_dir, tmp_path):
        _run(plugin, source_dir, StubGovernor(""))
        content = _written()[0].read_text(encoding="utf-8")
        assert "from pkg.calc import scale" in content
        assert str(tmp_path) not in content, "an absolute filesystem path became a module name"

    def test_an_unimportable_target_skips_rather_than_passes(self, plugin, source_dir):
        """``except ImportError: pass`` would make the generated test green
        having called nothing — the exact false pass this suite exists for."""
        _run(plugin, source_dir, StubGovernor(""))
        content = _written()[0].read_text(encoding="utf-8")
        assert 'pytest.importorskip("pkg.calc")' in content
        assert "ImportError" not in content


class TestNothingToFuzzIsNotAPass:
    def test_an_empty_tree_is_unknown(self, plugin, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        empty = tmp_path / "empty"
        empty.mkdir()
        result = _run(plugin, empty, StubGovernor(""))
        assert result.status == "unknown"
        assert _written() == []

    def test_a_module_of_zero_argument_functions_is_unknown(self, plugin, tmp_path, monkeypatch):
        """``@given()`` with no strategies is a Hypothesis error that
        ``ast.parse`` cannot see, so these are filtered before generation."""
        monkeypatch.chdir(tmp_path)
        root = write_source_tree(tmp_path / "src", {"pkg/calc.py": "def now():\n    return 1\n"})
        assert _run(plugin, root, StubGovernor("")).status == "unknown"

    def test_a_dry_run_with_nothing_to_fuzz_is_unknown(self, plugin, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        empty = tmp_path / "empty"
        empty.mkdir()
        assert _run(plugin, empty, StubGovernor(""), dry_run=True).status == "unknown"


class TestTheVirtualenvIsNotFuzzed:
    def test_site_packages_functions_are_not_targeted(self, plugin, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        root = write_source_tree(
            tmp_path / "src",
            {
                "pkg/calc.py": _ONE_FUNCTION,
                ".venv/lib/python3.12/site-packages/dep/core.py": _VENDORED,
                "node_modules/thing/setup.py": _VENDORED,
            },
        )
        result = _run(plugin, root, StubGovernor(""))
        targeted = result.findings[0]["targeted_functions"]
        assert [entry["function"] for entry in targeted] == ["scale"]


class TestItStillGenerates:
    """Positive controls. Without these, a plugin returning ``unknown``
    unconditionally would pass every test above."""

    def test_a_healthy_run_passes_and_writes_a_parseable_module(self, plugin, source_dir):
        result = _run(plugin, source_dir, StubGovernor(""))
        assert result.status == "pass"
        assert result.findings[0]["generated_count"] == 1
        ast.parse(_written()[0].read_text(encoding="utf-8"))

    def test_a_usable_hint_is_used(self, plugin, source_dir):
        """The validator must not reject everything — that would be "safe" and
        useless, and nothing else here would notice."""
        result = _run(plugin, source_dir, StubGovernor("x=st.integers(min_value=0)"))
        assert result.status == "pass"
        assert "@given(x=st.integers(min_value=0))" in _written()[0].read_text(encoding="utf-8")

    def test_the_model_was_actually_asked(self, plugin, source_dir):
        governor = StubGovernor("x=st.integers()")
        _run(plugin, source_dir, governor)
        assert governor.calls == 1

    def test_a_dry_run_lists_its_targets(self, plugin, source_dir):
        result = _run(plugin, source_dir, StubGovernor(""), dry_run=True)
        assert result.status == "pass"
        assert result.dry_run is True
        assert result.findings[0]["count"] == 1
        assert _written() == []


class TestTheHintValidator:
    """The gate on its own, so a regression in it is legible without reading a
    generated file."""

    @pytest.fixture
    def validate(self):
        return load_module("property_fuzz.plugin.py")._validated_strategies

    @pytest.mark.parametrize(
        "fragment",
        [
            "x=st.integers()",
            "x=st.integers(min_value=0),",
            "x=st.one_of(st.text(), st.none())",
        ],
    )
    def test_a_well_formed_fragment_is_kept(self, validate, fragment):
        assert validate(fragment, ["x"]) == fragment.rstrip(",")

    @pytest.mark.parametrize(
        "fragment",
        [
            "",
            "   ",
            "Use st.integers() for x.",
            "x=st.integers(",
            "st.integers()",  # positional, not a keyword
            "y=st.text()",  # names a parameter the function does not have
            "x=st.integers(), y=st.text()",  # one parameter too many
            "x=os.system('id')",  # a call that is not a strategy
            "x=st.integers().map(lambda v: __import__('os'))",
            "x=[st.integers() for _ in range(3)]",
        ],
    )
    def test_anything_else_is_rejected(self, validate, fragment):
        assert validate(fragment, ["x"]) == ""
