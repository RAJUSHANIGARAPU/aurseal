"""The GraphQL layer: one client, configured once, used by every service."""

from .graphql_client import GraphQLClient, GraphQLResponseError

__all__ = ["GraphQLClient", "GraphQLResponseError"]
