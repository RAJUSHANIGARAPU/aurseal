"""Flow graph — nodes are pages, edges are user actions between them."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web_discovery.parser.models import PageSpec


@dataclass
class PageNode:
    url: str
    title: str
    depth: int
    form_count: int
    link_count: int
    element_count: int
    has_auth_form: bool = False


@dataclass
class FlowEdge:
    source: str
    target: str
    action_type: str  # navigate, form_submit, click
    element_selector: str = ""
    element_text: str = ""


@dataclass
class FlowGraph:
    root: str
    nodes: dict[str, PageNode] = field(default_factory=dict)
    edges: list[FlowEdge] = field(default_factory=list)
    crawled_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())

    def add_page(self, spec: PageSpec) -> None:
        from web_discovery.crawler.session import normalise_url

        url = normalise_url(spec.url) or spec.url
        self.nodes[url] = PageNode(
            url=url,
            title=spec.title,
            depth=spec.depth,
            form_count=len(spec.forms),
            link_count=len(spec.links),
            element_count=len(spec.all_elements),
            has_auth_form=_is_auth_form_page(spec),
        )

        # Edges: link → target page (target added lazily)
        for link in spec.links:
            target_norm = normalise_url(link.href) or link.href
            if target_norm and target_norm != url:
                self.edges.append(
                    FlowEdge(
                        source=url,
                        target=target_norm,
                        action_type="navigate",
                        element_selector=link.selector,
                        element_text=link.text_content[:80],
                    )
                )

        # Edges: form submit
        for form in spec.forms:
            if form.submit_selector:
                self.edges.append(
                    FlowEdge(
                        source=url,
                        target=form.action or url,
                        action_type="form_submit",
                        element_selector=form.submit_selector,
                    )
                )

    def paths_from_root(self) -> list[list[str]]:
        """BFS paths from root to leaf nodes. Used for scenario generation."""
        from collections import deque

        if not self.nodes:
            return []

        adj: dict[str, list[str]] = {}
        for edge in self.edges:
            adj.setdefault(edge.source, []).append(edge.target)

        paths: list[list[str]] = []
        queue: deque[list[str]] = deque([[self.root]])

        while queue:
            path = queue.popleft()
            current = path[-1]

            children = [t for t in adj.get(current, []) if t in self.nodes and t not in path]
            if not children:
                paths.append(path)
                continue
            for child in children[:5]:  # cap fan-out per node
                queue.append([*path, child])

        return paths[:20]  # cap total paths

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "crawled_at": self.crawled_at,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": {
                url: {
                    "title": n.title,
                    "depth": n.depth,
                    "forms": n.form_count,
                    "links": n.link_count,
                    "elements": n.element_count,
                    "auth_form": n.has_auth_form,
                }
                for url, n in self.nodes.items()
            },
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "action": e.action_type,
                    "selector": e.element_selector,
                    "text": e.element_text,
                }
                for e in self.edges
            ],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


class FlowGraphBuilder:
    """Constructs a FlowGraph from a list of PageSpec objects."""

    def build(self, specs: list[PageSpec], root_url: str) -> FlowGraph:
        graph = FlowGraph(root=root_url)
        for spec in specs:
            graph.add_page(spec)
        return graph


def _is_auth_form_page(spec: PageSpec) -> bool:
    for form in spec.forms:
        for field_el in form.fields:
            if field_el.element_type == "password":
                return True
    return False
