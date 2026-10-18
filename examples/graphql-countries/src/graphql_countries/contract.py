"""The schema snapshot: load it, compare it with a live endpoint, re-record it on purpose.

``schema.graphql`` starts with ``# source: <endpoint>``, so a drift check never compares a
snapshot with an endpoint it was not recorded from. Refresh it after an intended change::

    PYTHONPATH=src uv run python -m <package>.contract --record
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path
from typing import cast

from graphql import (
    BreakingChange,
    DangerousChange,
    GraphQLSchema,
    IntrospectionQuery,
    build_client_schema,
    build_schema,
    find_breaking_changes,
    find_dangerous_changes,
    get_introspection_query,
    lexicographic_sort_schema,
    print_schema,
)

from .client import GraphQLClient

SNAPSHOT = Path(__file__).resolve().parents[2] / "schema.graphql"
SOURCE = "# source: "


def snapshot_source(path: Path = SNAPSHOT) -> str:
    """The endpoint the snapshot was recorded from, or ``""`` when it was not recorded."""
    first = path.read_text(encoding="utf-8").partition("\n")[0]
    return first.removeprefix(SOURCE).strip() if first.startswith(SOURCE) else ""


def load_snapshot(path: Path = SNAPSHOT) -> GraphQLSchema:
    return build_schema(path.read_text(encoding="utf-8"))


def introspect(client: GraphQLClient) -> GraphQLSchema:
    """The live schema. Descriptions and deprecated input fields are left out on purpose:
    the snapshot is a contract on shape, and some servers reject the newer introspection."""
    query = get_introspection_query(descriptions=False, input_value_deprecation=False)
    return build_client_schema(cast(IntrospectionQuery, client.execute(query)))


def to_sdl(schema: GraphQLSchema) -> str:
    """Stable SDL: types and fields sorted, so a diff shows changes, not reordering."""
    return print_schema(lexicographic_sort_schema(schema)) + "\n"


def changes(old: GraphQLSchema, new: GraphQLSchema) -> list[BreakingChange | DangerousChange]:
    """What breaks clients first, then what might."""
    return [*find_breaking_changes(old, new), *find_dangerous_changes(old, new)]


def diff(old: GraphQLSchema, new: GraphQLSchema) -> str:
    """A unified diff of the two schemas' SDL; empty when they are the same."""
    return "".join(
        difflib.unified_diff(
            to_sdl(old).splitlines(keepends=True),
            to_sdl(new).splitlines(keepends=True),
            fromfile="schema.graphql",
            tofile="live",
        )
    )


def record(client: GraphQLClient, url: str, path: Path = SNAPSHOT) -> None:
    path.write_text(f"{SOURCE}{url}\n\n{to_sdl(introspect(client))}", encoding="utf-8")


def main() -> None:
    from .settings import get_settings

    parser = argparse.ArgumentParser(description="Compare or re-record schema.graphql")
    parser.add_argument("--record", action="store_true", help="overwrite schema.graphql")
    args = parser.parse_args()
    settings = get_settings()
    client = GraphQLClient(settings.api_base_url, timeout_s=settings.timeout_s)
    if args.record:
        record(client, settings.api_base_url)
        print(f"recorded {SNAPSHOT.name} from {settings.api_base_url}")
        return
    print(diff(load_snapshot(), introspect(client)) or "no drift")


if __name__ == "__main__":
    main()
