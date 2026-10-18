"""Generators driven by a declared workflow (state-machine) definition.

* :mod:`generators.workflow_model` - the neutral workflow YAML, parsed into frozen dataclasses;
* :mod:`generators.state_graph` - adjacency graph, path enumeration, unreachable states, ASCII;
* :mod:`generators.spec_writer` - a Markdown spec in the ``specs/`` format;
* :mod:`generators.config_suite` - pytest cases (render, reachability, traversal paths);
* :mod:`generators.yaml_suite` - a ``pytest_generate_tests`` hook over registered YAML files;
* :mod:`generators.cli` - ``python -m generators.cli`` (spec, graph, generate, convert).
"""
