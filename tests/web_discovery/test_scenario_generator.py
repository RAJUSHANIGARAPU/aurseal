"""Tests for ScenarioGenerator."""

from web_discovery.data_generator.generator import DataGenerator
from web_discovery.flow_builder.graph import FlowGraph
from web_discovery.parser.models import ElementSpec, FormSpec, PageSpec
from web_discovery.scenario_generator.generator import ScenarioGenerator
from web_discovery.scenario_generator.models import ScenarioType


def _el(element_type="text", name="field", required=False):
    return ElementSpec(
        tag="input",
        element_type=element_type,
        selector=f"[name={name!r}]",
        name=name,
        required=required,
    )


def _form(fields=None, with_submit=True, required_fields=None):
    all_fields = list(fields or [_el(name="email", element_type="email")])
    if required_fields:
        for f in required_fields:
            f.required = True
    return FormSpec(
        action="/submit",
        method="POST",
        name="test",
        fields=all_fields,
        submit_selector="button[type=submit]" if with_submit else "",
    )


def _page(url="http://example.com", title="Home", forms=None):
    return PageSpec(url=url, title=title, forms=forms or [])


def _gen():
    return ScenarioGenerator(DataGenerator())


def _empty_graph(root="http://example.com"):
    return FlowGraph(root=root)


class TestSmokeScenario:
    def test_generates_smoke_for_each_page(self):
        specs = [_page("http://example.com/a"), _page("http://example.com/b")]
        scenarios = _gen().generate(specs, _empty_graph())
        smoke = [s for s in scenarios if s.scenario_type == ScenarioType.SMOKE]
        assert len(smoke) == 2

    def test_smoke_has_navigate_and_assert(self):
        specs = [_page()]
        scenarios = _gen().generate(specs, _empty_graph())
        smoke = next(s for s in scenarios if s.scenario_type == ScenarioType.SMOKE)
        types = [st.step_type for st in smoke.steps]
        assert "navigate" in types
        assert "assert_visible" in types

    def test_smoke_with_title_adds_assert_title(self):
        specs = [_page(title="Dashboard")]
        scenarios = _gen().generate(specs, _empty_graph())
        smoke = next(s for s in scenarios if s.scenario_type == ScenarioType.SMOKE)
        types = [st.step_type for st in smoke.steps]
        assert "assert_title" in types

    def test_smoke_without_title_skips_assert_title(self):
        specs = [_page(title="")]
        scenarios = _gen().generate(specs, _empty_graph())
        smoke = next(s for s in scenarios if s.scenario_type == ScenarioType.SMOKE)
        types = [st.step_type for st in smoke.steps]
        assert "assert_title" not in types


class TestHappyPath:
    def test_generates_happy_path_per_form(self):
        specs = [_page(forms=[_form(), _form()])]
        scenarios = _gen().generate(specs, _empty_graph())
        happy = [s for s in scenarios if s.scenario_type == ScenarioType.HAPPY_PATH]
        assert len(happy) >= 2

    def test_happy_path_has_fill_step(self):
        specs = [_page(forms=[_form(fields=[_el(name="username")])])]
        scenarios = _gen().generate(specs, _empty_graph())
        happy = next(s for s in scenarios if s.scenario_type == ScenarioType.HAPPY_PATH)
        types = [st.step_type for st in happy.steps]
        assert "fill" in types

    def test_happy_path_ends_with_assert_no_error_when_submit(self):
        specs = [_page(forms=[_form(with_submit=True)])]
        scenarios = _gen().generate(specs, _empty_graph())
        happy = next(s for s in scenarios if s.scenario_type == ScenarioType.HAPPY_PATH)
        assert happy.steps[-1].step_type == "assert_no_error"

    def test_select_field_uses_select_step(self):
        specs = [_page(forms=[_form(fields=[_el(element_type="select", name="country")])])]
        scenarios = _gen().generate(specs, _empty_graph())
        happy = next(s for s in scenarios if s.scenario_type == ScenarioType.HAPPY_PATH)
        types = [st.step_type for st in happy.steps]
        assert "select" in types

    def test_checkbox_uses_check_step(self):
        specs = [_page(forms=[_form(fields=[_el(element_type="checkbox", name="agree")])])]
        scenarios = _gen().generate(specs, _empty_graph())
        happy = next(s for s in scenarios if s.scenario_type == ScenarioType.HAPPY_PATH)
        types = [st.step_type for st in happy.steps]
        assert "check" in types


class TestNegativeScenario:
    def test_generates_negative_for_required_fields(self):
        req_field = _el(name="email", required=True)
        form = _form(fields=[req_field])
        specs = [_page(forms=[form])]
        scenarios = _gen().generate(specs, _empty_graph())
        neg = [s for s in scenarios if s.scenario_type == ScenarioType.NEGATIVE]
        assert len(neg) >= 1

    def test_negative_skipped_when_no_required_fields(self):
        specs = [_page(forms=[_form(fields=[_el(name="optional", required=False)])])]
        scenarios = _gen().generate(specs, _empty_graph())
        neg = [s for s in scenarios if s.scenario_type == ScenarioType.NEGATIVE]
        assert neg == []

    def test_negative_asserts_validation_error(self):
        req = _el(name="req", required=True)
        form = _form(fields=[req])
        specs = [_page(forms=[form])]
        scenarios = _gen().generate(specs, _empty_graph())
        neg = next(s for s in scenarios if s.scenario_type == ScenarioType.NEGATIVE)
        types = [st.step_type for st in neg.steps]
        assert "assert_visible" in types


class TestEdgeCase:
    def test_edge_case_generated_for_text_fields(self):
        specs = [_page(forms=[_form(fields=[_el(element_type="text", name="q")])])]
        scenarios = _gen().generate(specs, _empty_graph())
        edge = [s for s in scenarios if s.scenario_type == ScenarioType.EDGE_CASE]
        assert len(edge) >= 1

    def test_edge_case_has_256_char_value(self):
        specs = [_page(forms=[_form(fields=[_el(element_type="text", name="q")])])]
        scenarios = _gen().generate(specs, _empty_graph())
        edge = next(s for s in scenarios if s.scenario_type == ScenarioType.EDGE_CASE)
        values = [st.value for st in edge.steps if st.step_type == "fill"]
        assert any(len(v) == 256 for v in values)

    def test_edge_case_includes_xss_probe(self):
        specs = [_page(forms=[_form(fields=[_el(element_type="text", name="q")])])]
        scenarios = _gen().generate(specs, _empty_graph())
        edge = next(s for s in scenarios if s.scenario_type == ScenarioType.EDGE_CASE)
        values = [st.value for st in edge.steps if st.step_type == "fill"]
        assert any("<script>" in v for v in values)


class TestScenarioIds:
    def test_ids_are_unique(self):
        specs = [_page(forms=[_form()]), _page(url="http://example.com/b", forms=[_form()])]
        scenarios = _gen().generate(specs, _empty_graph())
        ids = [s.id for s in scenarios]
        assert len(ids) == len(set(ids))

    def test_result_excludes_empty_step_scenarios(self):
        specs = [_page(forms=[_form(with_submit=False, fields=[])])]
        scenarios = _gen().generate(specs, _empty_graph())
        for s in scenarios:
            assert len(s.steps) > 0
