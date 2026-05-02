# /graphify — Knowledge Graph for wiki_project

Builds an interactive knowledge graph of this codebase using [graphifyy](https://github.com/safishamsi/graphify) and answers questions about it via Claude.

## What it does
1. Runs `graphify build` on the project root (or `--update` if a graph already exists)
2. Produces `graph.html` (interactive vis.js graph) and `graph.json` (queryable data)
3. Exposes `GraphifyClient` and `KnowledgeGraphQuery` from `core/graphify/`

## Usage inside Claude Code

```
/graphify .                          # build graph on current directory
/graphify --update                   # incremental update (SHA256-cached)
/graphify --query "who calls AutoFixer?"
/graphify --gaps                     # test coverage gap analysis
/graphify --explain core/ai/auto_fixer.py
/graphify --path TraceAnalyzer AutoFixer
/graphify --stats                    # node / edge counts
```

## Python API

```python
from core.graphify import GraphifyClient, KnowledgeGraphQuery

client = GraphifyClient()
client.build()                               # or client.update()

qe = KnowledgeGraphQuery(client)
print(qe.find_test_coverage_gaps())
print(qe.explain_module("autonomous_ui.healer"))
print(qe.locator_impact("[data-testid='search-box']"))
```

## Standalone script

```bash
python scripts/build_knowledge_graph.py
python scripts/build_knowledge_graph.py --query "what does the memory plugin do?"
python scripts/build_knowledge_graph.py --gaps
```

## pytest integration

Activate the optional plugin by setting `ENABLE_GRAPHIFY=true`. It updates the graph
before the session starts and prints stats in the terminal summary.

```bash
ENABLE_GRAPHIFY=true pytest --env=development
```

A `knowledge_graph` session-scoped fixture is also available for tests that need
graph-level assertions.

## Dependencies

`graphifyy` must be installed:
```bash
pip install graphifyy
```

It is listed in `requirements.txt` and uses `ANTHROPIC_API_KEY` for semantic extraction.
