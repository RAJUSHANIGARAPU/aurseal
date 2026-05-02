"""Optional pytest plugin that builds/updates the knowledge graph before a test session."""

import logging
import os

import pytest

from .client import GraphifyClient

logger = logging.getLogger("wiki_project.graphify")


class GraphifyPlugin:
    """Registers as 'graphify-knowledge-graph' in pytest.

    Activated by setting ENABLE_GRAPHIFY=true.
    On session start it runs an incremental graph update (or full build if no graph exists).
    """

    def __init__(self, project_root: str = "."):
        self.client = GraphifyClient(project_root)

    @classmethod
    def from_env(cls) -> "GraphifyPlugin":
        root = os.environ.get("GRAPHIFY_ROOT", ".")
        return cls(project_root=root)

    def pytest_sessionstart(self, session) -> None:  # noqa: ARG002
        if not self.client.is_built():
            logger.info("graphify: no existing graph — running full build …")
            result = self.client.build()
        else:
            logger.info("graphify: updating knowledge graph …")
            result = self.client.update()

        if result.success:
            stats = self.client.graph_stats()
            logger.info(
                "graphify: graph ready — %d nodes, %d edges", stats["nodes"], stats["edges"]
            )
        else:
            logger.warning("graphify: build/update failed — %s", result.stderr[:200])

    def pytest_terminal_summary(self, terminalreporter, exitstatus) -> None:  # noqa: ARG002
        if not self.client.is_built():
            return
        stats = self.client.graph_stats()
        terminalreporter.write_sep("-", "graphify knowledge graph")
        terminalreporter.write_line(f"  nodes : {stats['nodes']}")
        terminalreporter.write_line(f"  edges : {stats['edges']}")
        if stats.get("graph_html"):
            terminalreporter.write_line(f"  html  : {stats['graph_html']}")


@pytest.fixture(scope="session")
def knowledge_graph(request) -> GraphifyClient:  # noqa: ARG001
    """Provides a ready GraphifyClient to tests that need graph-level queries."""
    client = GraphifyClient()
    if not client.is_built():
        pytest.skip("Knowledge graph not built. Run with ENABLE_GRAPHIFY=true first.")
    return client
