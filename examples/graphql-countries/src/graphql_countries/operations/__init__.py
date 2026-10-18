"""Operations live next to this file as ``*.graphql``, one named operation per file.

Fragments go in the file that uses them. Kept out of Python strings so editors, linters and
the offline tier can read them: every file is validated against the schema snapshot offline.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from graphql import DocumentNode, GraphQLError, GraphQLSchema, OperationDefinitionNode, parse
from graphql import validate as validate_document

HERE = Path(__file__).resolve().parent


def names() -> list[str]:
    """Every operation file, by stem."""
    return sorted(path.stem for path in HERE.glob("*.graphql"))


@cache
def document(name: str) -> DocumentNode:
    """The parsed operation ``<name>.graphql``; raises ``GraphQLError`` on bad syntax."""
    return parse((HERE / f"{name}.graphql").read_text(encoding="utf-8"))


def operation_names(name: str) -> list[str]:
    """Names of the operations defined in one file (fragments excluded)."""
    return [
        definition.name.value if definition.name else ""
        for definition in document(name).definitions
        if isinstance(definition, OperationDefinitionNode)
    ]


def validate(schema: GraphQLSchema, name: str) -> list[GraphQLError]:
    """Everything wrong with operation ``name`` against ``schema``; empty when it fits."""
    return validate_document(schema, document(name))
