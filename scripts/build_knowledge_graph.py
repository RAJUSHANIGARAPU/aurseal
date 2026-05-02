#!/usr/bin/env python3
"""CLI script: build or query the project knowledge graph via graphifyy.

Usage:
    python scripts/build_knowledge_graph.py               # full build
    python scripts/build_knowledge_graph.py --update      # incremental update
    python scripts/build_knowledge_graph.py --deep        # deep extraction mode
    python scripts/build_knowledge_graph.py --query "..."  # ask a question
    python scripts/build_knowledge_graph.py --gaps        # find test coverage gaps
    python scripts/build_knowledge_graph.py --explain core/graphify/client.py
    python scripts/build_knowledge_graph.py --path A B   # dependency path
    python scripts/build_knowledge_graph.py --stats       # print graph stats
"""

import argparse
import sys
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.graphify import GraphifyClient, KnowledgeGraphQuery


def main() -> None:
    parser = argparse.ArgumentParser(description="Build / query the wiki_project knowledge graph")
    parser.add_argument("--root", default=".", help="Project root (default: cwd)")
    parser.add_argument("--update", action="store_true", help="Incremental update only")
    parser.add_argument("--deep", action="store_true", help="Deep extraction mode")
    parser.add_argument("--stats", action="store_true", help="Print graph statistics")
    parser.add_argument("--query", metavar="QUESTION", help="Ask a natural-language question")
    parser.add_argument("--gaps", action="store_true", help="Find test coverage gaps")
    parser.add_argument("--explain", metavar="MODULE", help="Explain a module")
    parser.add_argument("--locator", metavar="SELECTOR", help="Find usages of a CSS/XPath selector")
    parser.add_argument(
        "--path", nargs=2, metavar=("A", "B"), help="Shortest path between two nodes"
    )
    args = parser.parse_args()

    client = GraphifyClient(project_root=args.root)
    qe = KnowledgeGraphQuery(client=client)

    # ---- Query operations (require existing graph) ----
    if args.query:
        print(qe.raw_query(args.query))
        return

    if args.gaps:
        print(qe.find_test_coverage_gaps())
        return

    if args.explain:
        print(qe.explain_module(args.explain))
        return

    if args.locator:
        print(qe.locator_impact(args.locator))
        return

    if args.path:
        print(qe.dependency_chain(args.path[0], args.path[1]))
        return

    if args.stats:
        stats = client.graph_stats()
        if not stats["built"]:
            print("Graph not built yet. Run without --stats first.")
        else:
            print(f"nodes : {stats['nodes']}")
            print(f"edges : {stats['edges']}")
            print(f"json  : {stats['graph_json']}")
            if stats.get("graph_html"):
                print(f"html  : {stats['graph_html']}")
        return

    # ---- Build / update ----
    if args.update and client.is_built():
        print("Updating knowledge graph …")
        result = client.update()
    else:
        mode = "deep" if args.deep else "standard"
        print(f"Building knowledge graph (mode={mode}) …")
        result = client.build(mode=mode)

    if result.success:
        stats = client.graph_stats()
        print(f"Done. {stats['nodes']} nodes, {stats['edges']} edges.")
        if result.graph_html:
            print(f"Interactive graph: {result.graph_html}")
    else:
        print("Build failed.")
        if result.stderr:
            print(result.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
