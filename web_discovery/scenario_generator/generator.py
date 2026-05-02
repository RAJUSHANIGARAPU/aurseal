"""Scenario generator — converts flow graph + page specs into test scenarios."""

from __future__ import annotations

from typing import TYPE_CHECKING

from web_discovery.scenario_generator.models import (
    ScenarioStep,
    ScenarioType,
    TestScenario,
)

if TYPE_CHECKING:
    from web_discovery.data_generator.generator import DataGenerator
    from web_discovery.flow_builder.graph import FlowGraph
    from web_discovery.parser.models import FormSpec, PageSpec


class ScenarioGenerator:
    """Generates test scenarios from discovered pages and flow graph."""

    def __init__(self, data_generator: DataGenerator) -> None:
        self._data_gen = data_generator
        self._id_counter = 0

    def generate(
        self,
        specs: list[PageSpec],
        graph: FlowGraph,
    ) -> list[TestScenario]:
        scenarios: list[TestScenario] = []

        for spec in specs:
            # Smoke: page loads and title is non-empty
            scenarios.append(self._smoke_scenario(spec))

            # Happy path: fill each form with valid data and submit
            for form in spec.forms:
                scenarios.append(self._happy_path_form(spec, form))

            # Negative: submit empty required fields
            required_forms = [f for f in spec.forms if f.required_fields]
            for form in required_forms:
                scenarios.append(self._negative_empty_required(spec, form))

            # Edge case: boundary values for text inputs
            for form in spec.forms:
                if any(f.element_type in ("text", "search") for f in form.fields):
                    scenarios.append(self._edge_case_boundary(spec, form))

        # Navigation flow: walk through BFS paths
        for path in graph.paths_from_root():
            if len(path) >= 2:
                scenarios.append(self._navigation_flow(path, graph))

        return [s for s in scenarios if s.steps]

    # ------------------------------------------------------------------
    # Scenario builders
    # ------------------------------------------------------------------

    def _smoke_scenario(self, spec: PageSpec) -> TestScenario:
        scenario = self._new_scenario(
            name=f"Page loads — {spec.title or spec.url}",
            scenario_type=ScenarioType.SMOKE,
            spec=spec,
            tags=["smoke", "generated"],
        )
        scenario.steps = [
            ScenarioStep(
                step_type="navigate",
                description=f"Navigate to {spec.url}",
                url=spec.url,
            ),
            ScenarioStep(
                step_type="assert_visible",
                description="Page body is visible",
                selector="body",
            ),
        ]
        if spec.title:
            scenario.steps.append(
                ScenarioStep(
                    step_type="assert_title",
                    description=f"Page title contains '{spec.title[:40]}'",
                    assertion=spec.title[:40],
                )
            )
        return scenario

    def _happy_path_form(self, spec: PageSpec, form: FormSpec) -> TestScenario:
        test_data = {
            f.display_name: self._data_gen.generate_for_field(f)
            for f in form.fields
            if f.is_form_field()
        }
        scenario = self._new_scenario(
            name=f"Happy path form — {spec.title or spec.url}",
            scenario_type=ScenarioType.HAPPY_PATH,
            spec=spec,
            tags=["happy_path", "generated", "regression"],
        )
        scenario.test_data = test_data
        scenario.steps = [
            ScenarioStep(step_type="navigate", url=spec.url, description=f"Navigate to {spec.url}"),
        ]
        for field_el in form.fields:
            if not field_el.is_form_field():
                continue
            value = test_data.get(field_el.display_name, "")
            if field_el.element_type == "select":
                scenario.steps.append(
                    ScenarioStep(
                        step_type="select",
                        selector=field_el.selector,
                        value=value,
                        description=f"Select '{value}' in {field_el.display_name}",
                    )
                )
            elif field_el.element_type == "checkbox":
                scenario.steps.append(
                    ScenarioStep(
                        step_type="check",
                        selector=field_el.selector,
                        description=f"Check {field_el.display_name}",
                    )
                )
            else:
                scenario.steps.append(
                    ScenarioStep(
                        step_type="fill",
                        selector=field_el.selector,
                        value=value,
                        description=f"Fill {field_el.display_name} with '{value[:30]}'",
                    )
                )
        if form.submit_selector:
            scenario.steps.append(
                ScenarioStep(
                    step_type="click",
                    selector=form.submit_selector,
                    description="Submit form",
                )
            )
            scenario.steps.append(
                ScenarioStep(
                    step_type="assert_no_error",
                    description="No error message visible after submit",
                    selector="[role=alert], .error, .alert-error",
                    assertion="not_visible",
                )
            )
        return scenario

    def _negative_empty_required(self, spec: PageSpec, form: FormSpec) -> TestScenario:
        scenario = self._new_scenario(
            name=f"Negative — empty required fields — {spec.title or spec.url}",
            scenario_type=ScenarioType.NEGATIVE,
            spec=spec,
            tags=["negative", "generated"],
        )
        scenario.steps = [
            ScenarioStep(step_type="navigate", url=spec.url, description=f"Navigate to {spec.url}"),
        ]
        if form.submit_selector:
            scenario.steps.append(
                ScenarioStep(
                    step_type="click",
                    selector=form.submit_selector,
                    description="Submit form without filling required fields",
                )
            )
            scenario.steps.append(
                ScenarioStep(
                    step_type="assert_visible",
                    description="Validation error appears",
                    selector="[aria-invalid=true], .error, [role=alert], .invalid-feedback",
                )
            )
        return scenario

    def _edge_case_boundary(self, spec: PageSpec, form: FormSpec) -> TestScenario:
        scenario = self._new_scenario(
            name=f"Edge case — boundary values — {spec.title or spec.url}",
            scenario_type=ScenarioType.EDGE_CASE,
            spec=spec,
            tags=["edge_case", "generated"],
        )
        scenario.steps = [
            ScenarioStep(step_type="navigate", url=spec.url, description=f"Navigate to {spec.url}"),
        ]
        for field_el in form.fields:
            if field_el.element_type not in ("text", "search", "textarea"):
                continue
            # Max-length boundary: 256 chars
            scenario.steps.append(
                ScenarioStep(
                    step_type="fill",
                    selector=field_el.selector,
                    value="a" * 256,
                    description=f"Fill {field_el.display_name} with 256-char string",
                )
            )
            # Special characters
            scenario.steps.append(
                ScenarioStep(
                    step_type="fill",
                    selector=field_el.selector,
                    value="<script>alert(1)</script>",
                    description=f"Fill {field_el.display_name} with special chars (XSS probe)",
                )
            )
        if form.submit_selector and scenario.steps:
            scenario.steps.append(
                ScenarioStep(
                    step_type="click",
                    selector=form.submit_selector,
                    description="Submit with boundary values",
                )
            )
        return scenario

    def _navigation_flow(self, path: list[str], graph: FlowGraph) -> TestScenario:
        titles = [graph.nodes.get(u, type("", (), {"title": ""})()).title for u in path]
        label = " → ".join(t[:20] if t else u[-30:] for t, u in zip(titles, path, strict=False))
        scenario = self._new_scenario_raw(
            name=f"Navigation flow — {label}",
            scenario_type=ScenarioType.HAPPY_PATH,
            page_url=path[0],
            page_title=titles[0] if titles else "",
            tags=["navigation", "generated"],
        )
        for i, url in enumerate(path):
            scenario.steps.append(
                ScenarioStep(
                    step_type="navigate",
                    url=url,
                    description=f"Navigate to {titles[i] or url}",
                )
            )
            scenario.steps.append(
                ScenarioStep(
                    step_type="assert_visible",
                    selector="body",
                    description="Page loaded",
                )
            )
        return scenario

    # ------------------------------------------------------------------

    def _new_scenario(
        self,
        name: str,
        scenario_type: ScenarioType,
        spec: PageSpec,
        tags: list[str],
    ) -> TestScenario:
        return self._new_scenario_raw(
            name=name,
            scenario_type=scenario_type,
            page_url=spec.url,
            page_title=spec.title,
            tags=tags,
        )

    def _new_scenario_raw(
        self,
        name: str,
        scenario_type: ScenarioType,
        page_url: str,
        page_title: str,
        tags: list[str],
    ) -> TestScenario:
        self._id_counter += 1
        return TestScenario(
            id=f"wd_{self._id_counter:04d}",
            name=name,
            scenario_type=scenario_type,
            page_url=page_url,
            page_title=page_title,
            tags=tags,
        )
