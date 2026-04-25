"""Tests for ScriptGenerator."""

import re

from web_discovery.scenario_generator.models import ScenarioStep, ScenarioType, TestScenario
from web_discovery.script_generator.generator import ScriptGenerator, _to_func_name


def _scenario(
    sid="wd_0001",
    name="Test page loads",
    scenario_type=ScenarioType.SMOKE,
    url="http://example.com",
    steps=None,
    tags=None,
) -> TestScenario:
    return TestScenario(
        id=sid,
        name=name,
        scenario_type=scenario_type,
        page_url=url,
        page_title="Home",
        tags=tags or ["smoke"],
        steps=steps
        or [
            ScenarioStep(step_type="navigate", url=url, description=f"Navigate to {url}"),
            ScenarioStep(step_type="assert_visible", selector="body", description="Body visible"),
        ],
    )


class TestFuncName:
    def test_basic(self):
        assert _to_func_name("Page loads") == "test_page_loads"

    def test_special_chars_stripped(self):
        name = _to_func_name("Happy path — form submit!")
        assert name.startswith("test_")
        assert re.match(r"test_[a-z0-9_]+$", name)

    def test_truncated_at_80(self):
        long = "x" * 200
        assert len(_to_func_name(long)) <= 85  # test_ + 80


class TestScriptGenerator:
    def test_creates_output_dir(self, tmp_path):
        gen = ScriptGenerator(output_dir=tmp_path / "out")
        gen.generate([_scenario()], base_url="http://example.com")
        assert (tmp_path / "out").exists()

    def test_writes_test_file(self, tmp_path):
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([_scenario()], base_url="http://example.com")
        test_files = [p for p in written if p.name.startswith("test_wd_")]
        assert len(test_files) >= 1

    def test_writes_conftest(self, tmp_path):
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([_scenario()], base_url="http://example.com")
        conftest = [p for p in written if p.name == "conftest.py"]
        assert len(conftest) == 1

    def test_conftest_has_page_fixture(self, tmp_path):
        gen = ScriptGenerator(output_dir=tmp_path)
        gen.generate([_scenario()], base_url="")
        content = (tmp_path / "conftest.py").read_text()
        assert "def page(" in content

    def test_navigate_step_in_output(self, tmp_path):
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([_scenario()], base_url="http://example.com")
        test_file = next(p for p in written if p.name.startswith("test_wd_"))
        content = test_file.read_text()
        assert "page.goto(" in content

    def test_fill_step_in_output(self, tmp_path):
        steps = [
            ScenarioStep(step_type="navigate", url="http://example.com", description="go"),
            ScenarioStep(
                step_type="fill", selector="#name", value="Alice", description="fill name"
            ),
        ]
        scenario = _scenario(steps=steps)
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([scenario], base_url="http://example.com")
        test_file = next(p for p in written if p.name.startswith("test_wd_"))
        content = test_file.read_text()
        assert "fill(" in content

    def test_assert_visible_uses_expect(self, tmp_path):
        steps = [
            ScenarioStep(step_type="navigate", url="http://example.com", description="go"),
            ScenarioStep(step_type="assert_visible", selector="body", description="check"),
        ]
        scenario = _scenario(steps=steps)
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([scenario], base_url="http://example.com")
        test_file = next(p for p in written if p.name.startswith("test_wd_"))
        content = test_file.read_text()
        assert "expect(" in content

    def test_groups_by_page_url(self, tmp_path):
        s1 = _scenario(sid="wd_0001", url="http://example.com/a")
        s2 = _scenario(sid="wd_0002", url="http://example.com/b")
        s3 = _scenario(sid="wd_0003", url="http://example.com/a")
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([s1, s2, s3], base_url="http://example.com")
        test_files = [p for p in written if p.name.startswith("test_wd_")]
        assert len(test_files) == 2  # 2 unique URLs

    def test_empty_scenarios_returns_empty(self, tmp_path):
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([], base_url="")
        assert written == []

    def test_auto_generated_header_in_file(self, tmp_path):
        gen = ScriptGenerator(output_dir=tmp_path)
        written = gen.generate([_scenario()], base_url="http://example.com")
        test_file = next(p for p in written if p.name.startswith("test_wd_"))
        content = test_file.read_text()
        assert "AUTO-GENERATED" in content

    def test_existing_conftest_not_overwritten(self, tmp_path):
        existing = tmp_path / "conftest.py"
        existing.write_text("# my custom conftest")
        gen = ScriptGenerator(output_dir=tmp_path)
        gen.generate([_scenario()], base_url="")
        assert existing.read_text() == "# my custom conftest"
