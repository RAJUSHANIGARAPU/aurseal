"""Main discovery pipeline: crawl → parse → graph → scenarios → scripts."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DiscoveryOrchestrator:
    """Runs the full web discovery pipeline end-to-end."""

    def __init__(self, config=None) -> None:
        from web_discovery.config import DiscoveryConfig

        self._config = config or DiscoveryConfig.from_env()

    def run(self):
        """Execute the discovery pipeline and return a DiscoveryResult."""
        from playwright.sync_api import sync_playwright

        from web_discovery.crawler.engine import CrawlEngine
        from web_discovery.data_generator.generator import DataGenerator
        from web_discovery.flow_builder.graph import FlowGraphBuilder
        from web_discovery.memory_bridge import MemoryBridge
        from web_discovery.scenario_generator.generator import ScenarioGenerator
        from web_discovery.scenario_generator.models import DiscoveryResult
        from web_discovery.script_generator.generator import ScriptGenerator

        cfg = self._config
        run_id = uuid.uuid4().hex[:8]
        artifacts = Path(cfg.artifacts_dir) / run_id
        artifacts.mkdir(parents=True, exist_ok=True)

        logger.info(
            "[orchestrator] run=%s target=%s depth=%d pages=%d",
            run_id,
            cfg.target_url,
            cfg.max_depth,
            cfg.max_pages,
        )

        # 1. Crawl
        specs: list = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=cfg.headless)
            try:
                engine = CrawlEngine(cfg)
                specs = engine.crawl(browser)
            finally:
                browser.close()

        logger.info("[orchestrator] crawled %d pages", len(specs))

        # 2. Build flow graph
        builder = FlowGraphBuilder()
        graph = builder.build(specs, cfg.target_url)
        graph_path = artifacts / "flow_graph.json"
        graph.save(graph_path)
        logger.info("[orchestrator] graph: %d nodes, %d edges", len(graph.nodes), len(graph.edges))

        # 3. Generate scenarios
        data_gen = DataGenerator()
        scenario_gen = ScenarioGenerator(data_gen)
        scenarios = scenario_gen.generate(specs, graph)
        logger.info("[orchestrator] generated %d scenarios", len(scenarios))

        # 4. Optional: record into MemPalace
        bridge = MemoryBridge(enabled=cfg.llm_enabled)
        if bridge.available:
            bridge.record_scenarios(scenarios)

        # 5. Generate test scripts
        output_dir = Path(cfg.output_dir) / run_id
        script_gen = ScriptGenerator(output_dir=output_dir)
        written = script_gen.generate(scenarios, base_url=cfg.target_url)
        logger.info("[orchestrator] wrote %d test file(s) to %s", len(written), output_dir)

        # 6. Build result
        result = DiscoveryResult(
            target_url=cfg.target_url,
            run_id=run_id,
            pages_crawled=len(specs),
            scenarios=scenarios,
            generated_files=[str(p) for p in written],
            graph_path=str(graph_path),
            artifacts_dir=str(artifacts),
        )

        # 7. Save index
        result.save_index(artifacts / "index.json")

        logger.info("[orchestrator] %s", result.summary())
        return result
