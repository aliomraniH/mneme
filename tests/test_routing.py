from __future__ import annotations

from typing import Any

import pytest

from agent_service.routing import route_to_namespace


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
        # sql keyword → pg_main (no saaz keywords)
        ("query", {"sql": "SELECT * FROM users"}, "pg_main"),
        ("run_sql", {}, "pg_main"),
        # pg_ prefix → pg_main
        ("pg_query", {}, "pg_main"),
        # pinecone
        ("search_pinecone_index", {}, "pinecone_main"),
        ("embedding_index_query", {}, "pinecone_main"),
        # default fallback
        ("unknown_tool", {}, "default"),
        ("", {}, "default"),
        ("list_tables", {}, "default"),  # no keyword matches
        # stats tool has no keywords → default
        ("stats", {}, "default"),
        # genre keyword → saaz_demo
        ("query", {"sql": "SELECT genre FROM tracks"}, "saaz_demo"),
    ],
)
def test_route_to_namespace(tool_name: str, params: dict[str, Any], expected: str) -> None:
    assert route_to_namespace(tool_name, params) == expected


def test_route_does_not_raise_on_non_serializable_params() -> None:
    """_safe_dumps must not propagate exceptions."""
    result = route_to_namespace("some_tool", {"obj": object()})
    assert isinstance(result, str)
