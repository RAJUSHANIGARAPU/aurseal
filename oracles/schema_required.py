"""Read-only: does the schema the service validates against declare the required inputs?

Some services serve the configuration they validate requests against (an
action's input schema, a form definition). Reading it answers "is field X
required?" without sending a single write. :func:`missing_required` locates the
schema node and returns the names from ``required`` that it does not list as
required.

The control: the node must be *expanded*. A schema served as a reference, or
trimmed to its ``required`` list, does not declare the properties themselves,
and "not in required" would then mean "not read", not "not required". When none
of the expected names appears under ``properties``, :class:`NotExpanded` is
raised instead of a finding.

``locate`` is a node name (searched recursively, must be unique) or a callable
that returns the node. :func:`served_missing_required` runs the whole check
through a :class:`~oracles.data_health.GetOnlyApi`, so it cannot write.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from oracles.data_health import GetOnlyApi

Locate = str | Callable[[Any], Any]


class NotExpanded(AssertionError):
    """The schema node declares none of the expected properties; it was not expanded."""


class SchemaNotFound(LookupError):
    """No node (or more than one) carries the name being located."""


def find_nodes(document: Any, name: str) -> list[Mapping[str, Any]]:
    """Every mapping value stored under key ``name`` anywhere in ``document``."""
    found: list[Mapping[str, Any]] = []
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key == name and isinstance(value, Mapping):
                    found.append(value)
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def locate_node(document: Any, locate: Locate) -> Mapping[str, Any]:
    if callable(locate):
        node = locate(document)
        if not isinstance(node, Mapping):
            raise SchemaNotFound(f"locator returned {type(node).__name__}, not a schema object")
        return node
    nodes = find_nodes(document, locate)
    if len(nodes) != 1:
        raise SchemaNotFound(f"{len(nodes)} nodes named {locate!r}; expected exactly one")
    return nodes[0]


def missing_required(document: Any, locate: Locate, required: Iterable[str]) -> list[str]:
    """Names in ``required`` the located schema does not list as required."""
    wanted = list(required)
    if not wanted:
        raise ValueError("missing_required needs at least one field name")
    node = locate_node(document, locate)
    properties = node.get("properties")
    declared = set(properties) if isinstance(properties, Mapping) else set()
    if not declared & set(wanted):
        raise NotExpanded(
            f"none of {wanted} is declared under 'properties' (declared: {sorted(declared)}): "
            "the schema was not expanded, so 'not required' cannot be read from it"
        )
    listed = node.get("required")
    listed = set(listed) if isinstance(listed, list) else set()
    return [name for name in wanted if name not in listed]


def served_missing_required(
    api: GetOnlyApi, path: str, locate: Locate, required: Iterable[str]
) -> list[str]:
    """GET the served schema document and run :func:`missing_required` on it."""
    response = api.get(path)
    if not response.is_success:
        raise AssertionError(f"served schema at {path} did not answer 2xx: {response}")
    return missing_required(response.json(), locate, required)
