from __future__ import annotations

from typing import Any

import pytest

from agent_service.routing import route_to_namespace

# ---------------------------------------------------------------------------
# Routing keywords for tests that need project-specific namespaces.
# In production these come from NAMESPACE_ROUTING_KEYWORDS / DB registry.
# ---------------------------------------------------------------------------
_SAAZ_KEYWORDS: dict[str, list[str]] = {
    "saaz_demo": ["artist", "song", "persian", "jazz", "saaz", "genre"],
    "pg_main": ["postgres", "pg_", "sql"],
    "pinecone_main": ["pinecone", "embedding_index"],
}


# ---------------------------------------------------------------------------
# Tests using default keywords (only generic infrastructure patterns)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool_name,params,expected",
    [
        # sql keyword → pg_main
        ("query", {"sql": "SELECT * FROM users"}, "pg_main"),
        ("run_sql", {}, "pg_main"),
        ("pg_query", {}, "pg_main"),
        # pinecone
        ("search_pinecone_index", {}, "pinecone_main"),
        ("embedding_index_query", {}, "pinecone_main"),
        # default fallback — no keyword matches
        ("unknown_tool", {}, "default"),
        ("", {}, "default"),
        ("list_tables", {}, "default"),
        ("stats", {}, "default"),
        # artist/song/genre don't match defaults (no saaz in defaults)
        ("search_artists", {}, "default"),
        ("list_artists", {"genre": "jazz"}, "default"),
    ],
)
def test_route_to_namespace_defaults(
    tool_name: str, params: dict[str, Any], expected: str
) -> None:
    """Default keywords only cover pg_main and pinecone_main."""
    assert route_to_namespace(tool_name, params) == expected


# ---------------------------------------------------------------------------
# Tests with explicit project-specific keywords (simulates production config)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool_name,params,expected",
    [
        # saaz keywords win on tool name
        ("search_artists", {}, "saaz_demo"),
        ("list_artists", {"genre": "jazz"}, "saaz_demo"),
        ("get_artist", {"name": "foo"}, "saaz_demo"),
        # saaz keyword in params
        ("query", {"sql": "SELECT * FROM artist WHERE genre = 'jazz'"}, "saaz_demo"),
        ("query", {"sql": "SELECT * FROM song LIMIT 10"}, "saaz_demo"),
        # genre keyword → saaz_demo
        ("query", {"sql": "SELECT genre FROM tracks"}, "saaz_demo"),
        # sql keyword → pg_main when no saaz keywords present
        ("query", {"sql": "SELECT * FROM users"}, "pg_main"),
        ("run_sql", {}, "pg_main"),
        ("pg_query", {}, "pg_main"),
        # pinecone
        ("search_pinecone_index", {}, "pinecone_main"),
        ("embedding_index_query", {}, "pinecone_main"),
        # default fallback
        ("unknown_tool", {}, "default"),
        ("", {}, "default"),
        ("list_tables", {}, "default"),
        ("stats", {}, "default"),
    ],
)
def test_route_to_namespace_with_custom_keywords(
    tool_name: str, params: dict[str, Any], expected: str
) -> None:
    """With explicit project keywords, saaz tools route to saaz_demo."""
    assert route_to_namespace(tool_name, params, namespace_keywords=_SAAZ_KEYWORDS) == expected


def test_route_does_not_raise_on_non_serializable_params() -> None:
    """_safe_dumps must not propagate exceptions."""
    result = route_to_namespace("some_tool", {"obj": object()})
    assert isinstance(result, str)
