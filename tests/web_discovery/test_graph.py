"""Tests for FlowGraph and FlowGraphBuilder."""

import json

from web_discovery.flow_builder.graph import FlowGraph, FlowGraphBuilder
from web_discovery.parser.models import ElementSpec, PageSpec


def _link_el(href: str) -> ElementSpec:
    return ElementSpec(
        tag="a",
        element_type="link",
        selector=f"a[href={href!r}]",
        text_content="Link",
        href=href,
    )


def _spec(url: str, title: str = "", links: list[str] | None = None) -> PageSpec:
    link_els = [_link_el(h) for h in (links or [])]
    return PageSpec(url=url, title=title, links=link_els)


class TestFlowGraph:
    def test_add_page_creates_node(self):
        graph = FlowGraph(root="http://example.com")
        spec = _spec("http://example.com", title="Home")
        graph.add_page(spec)
        assert any("example.com" in u for u in graph.nodes)

    def test_add_page_creates_link_edges(self):
        graph = FlowGraph(root="http://example.com")
        spec = _spec("http://example.com", links=["http://example.com/about"])
        graph.add_page(spec)
        assert any(e.action_type == "navigate" for e in graph.edges)

    def test_paths_from_root_returns_list(self):
        graph = FlowGraph(root="http://example.com")
        root_spec = _spec("http://example.com", links=["http://example.com/a"])
        child_spec = _spec("http://example.com/a")
        graph.add_page(root_spec)
        graph.add_page(child_spec)
        paths = graph.paths_from_root()
        assert isinstance(paths, list)

    def test_paths_from_root_empty_graph(self):
        graph = FlowGraph(root="http://example.com")
        assert graph.paths_from_root() == []

    def test_paths_cap_at_20(self):
        graph = FlowGraph(root="http://example.com")
        links = [f"http://example.com/page{i}" for i in range(30)]
        root_spec = _spec("http://example.com", links=links)
        graph.add_page(root_spec)
        for i in range(30):
            graph.add_page(_spec(f"http://example.com/page{i}"))
        paths = graph.paths_from_root()
        assert len(paths) <= 20

    def test_to_dict_structure(self):
        graph = FlowGraph(root="http://example.com")
        graph.add_page(_spec("http://example.com", title="Home"))
        d = graph.to_dict()
        assert "root" in d
        assert "nodes" in d
        assert "edges" in d
        assert "crawled_at" in d

    def test_save_writes_json(self, tmp_path):
        graph = FlowGraph(root="http://example.com")
        graph.add_page(_spec("http://example.com"))
        p = tmp_path / "graph.json"
        graph.save(p)
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["root"] == "http://example.com"

    def test_no_self_loop_edges(self):
        graph = FlowGraph(root="http://example.com")
        spec = _spec("http://example.com", links=["http://example.com"])
        graph.add_page(spec)
        self_loops = [e for e in graph.edges if e.source == e.target]
        assert self_loops == []


class TestFlowGraphBuilder:
    def test_build_returns_graph(self):
        specs = [_spec("http://example.com", links=["http://example.com/a"])]
        specs.append(_spec("http://example.com/a"))
        builder = FlowGraphBuilder()
        graph = builder.build(specs, root_url="http://example.com")
        assert isinstance(graph, FlowGraph)
        assert len(graph.nodes) >= 1
