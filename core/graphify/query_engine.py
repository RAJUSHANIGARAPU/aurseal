"""Claude-backed query engine over a graphify knowledge graph."""

import json
import os

import requests

from .client import GraphifyClient


class KnowledgeGraphQuery:
    """Uses Claude to answer questions grounded in the project's knowledge graph."""

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, client: GraphifyClient | None = None):
        self.client = client or GraphifyClient()
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = os.environ.get("GRAPHIFY_MODEL", self.DEFAULT_MODEL)

    # ------------------------------------------------------------------
    # High-level queries
    # ------------------------------------------------------------------

    def find_test_coverage_gaps(self) -> str:
        """Identify modules that have no corresponding tests in the knowledge graph."""
        return self._graph_query(
            "List all source modules that have no test counterpart. "
            "For each gap, suggest a minimal test scenario that would cover it."
        )

    def explain_module(self, module_name: str) -> str:
        """Explain what a module does and how it connects to the rest of the codebase."""
        return self._graph_query(
            f"Explain the purpose of '{module_name}', its dependencies, "
            f"and any modules that depend on it."
        )

    def locator_impact(self, selector: str) -> str:
        """Find all tests/page-objects that reference a given CSS/XPath selector."""
        return self._graph_query(
            f"Which page objects and test files reference the selector '{selector}'? "
            f"What tests would break if that selector changed?"
        )

    def dependency_chain(self, node_a: str, node_b: str) -> str:
        """Describe the connection path between two components."""
        path = self.client.shortest_path(node_a, node_b)
        if not path or "failed" in path.lower():
            return path
        header = f"The shortest path between '{node_a}' and '{node_b}' in the project graph is:\n"
        return self._claude_explain(
            header + f"{path}\n\n"
            "Explain what this dependency chain means and whether it presents any risk."
        )

    def raw_query(self, question: str) -> str:
        """Pass an arbitrary question to the graphify query engine."""
        return self.client.query(question)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _graph_query(self, question: str) -> str:
        """Query via graphify CLI first; fall back to Claude + raw graph context."""
        if not self.client.is_built():
            return "Knowledge graph not built yet. Run GraphifyClient().build() first."

        # Try the graphify CLI's built-in query
        cli_answer = self.client.query(question)
        if cli_answer and "failed" not in cli_answer.lower():
            return cli_answer

        # Fall back: load raw graph and ask Claude directly
        try:
            graph = self.client.load_graph()
        except FileNotFoundError as e:
            return str(e)

        summary = self._graph_summary(graph)
        return self._claude_explain(
            f"You are analysing a Python test-automation project (wiki_project).\n"
            f"Here is a summary of its knowledge graph:\n{summary}\n\n"
            f"Question: {question}"
        )

    def _graph_summary(self, graph: dict) -> str:
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        top_nodes = [n.get("id", n.get("label", str(n))) for n in nodes[:80]]
        return json.dumps(
            {"node_count": len(nodes), "edge_count": len(edges), "sample_nodes": top_nodes},
            indent=2,
        )

    def _claude_explain(self, prompt: str) -> str:
        if not self.api_key:
            return f"(Set ANTHROPIC_API_KEY for AI analysis)\n\nPrompt was:\n{prompt[:500]}"
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        except Exception as e:
            return f"Claude API error: {e}"
