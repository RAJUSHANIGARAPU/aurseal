"""Scenario and step data models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class ScenarioType(str, Enum):
    HAPPY_PATH = "happy_path"
    NEGATIVE = "negative"
    EDGE_CASE = "edge_case"
    SMOKE = "smoke"


@dataclass
class ScenarioStep:
    step_type: str  # navigate, click, fill, select, assert_url, assert_visible, assert_text
    description: str = ""
    selector: str = ""
    url: str = ""
    value: str = ""
    assertion: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in vars(self).items() if v}


@dataclass
class TestScenario:
    __test__ = False  # prevent pytest from collecting this as a test class

    id: str
    name: str
    scenario_type: ScenarioType
    page_url: str
    page_title: str
    tags: list[str] = field(default_factory=list)
    steps: list[ScenarioStep] = field(default_factory=list)
    test_data: dict = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.scenario_type.value,
            "url": self.page_url,
            "title": self.page_title,
            "tags": self.tags,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "test_data": self.test_data,
        }


@dataclass
class DiscoveryResult:
    """Full output of one discovery run."""

    target_url: str
    run_id: str
    pages_crawled: int
    scenarios: list[TestScenario] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    graph_path: str = ""
    artifacts_dir: str = ""

    def summary(self) -> str:
        happy = sum(1 for s in self.scenarios if s.scenario_type == ScenarioType.HAPPY_PATH)
        neg = sum(1 for s in self.scenarios if s.scenario_type == ScenarioType.NEGATIVE)
        edge = sum(1 for s in self.scenarios if s.scenario_type == ScenarioType.EDGE_CASE)
        return (
            f"[web-discovery] {self.pages_crawled} pages → "
            f"{len(self.scenarios)} scenarios "
            f"(happy={happy} neg={neg} edge={edge}) "
            f"→ {len(self.generated_files)} test file(s)"
        )

    def save_index(self, path) -> None:
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "target_url": self.target_url,
            "run_id": self.run_id,
            "pages_crawled": self.pages_crawled,
            "scenario_count": len(self.scenarios),
            "generated_files": self.generated_files,
            "graph_path": self.graph_path,
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
