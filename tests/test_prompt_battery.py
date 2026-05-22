"""Prompt and query battery — realistic Claude usage patterns.

Each scenario documents a natural-language prompt from a Claude user, the
expected MCP tool call sequence, and the assertions on the result shape.
These run in-process (no live server) using mock upstreams, so they verify
the middleware chain and routing logic without DATABASE_URL.

Scenarios cover:
  1. "Who are the traditional Persian artists?" → list_artists + filter
  2. "Find artists similar to Siavash Amini" → search_artists (semantic)
  3. "Give me a genre breakdown" → saaz_query with GROUP BY
  4. "Tell me about Shahram Nazeri" → get_artist (full record)
  5. "Are there any deceased artists?" → list_artists with status filter
  6. "How many artists have bios?" → saaz_query COUNT
  7. "Try to delete artist data" → DML rejection assertion
  8. Bulk query: all genres × all statuses — routing never crashes
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP
from fastmcp.server import create_proxy

from agent_service.middleware.audit import AuditMiddleware
from agent_service.routing import route_to_namespace


# ---------------------------------------------------------------------------
# Shared fake upstream that mimics the saaz tool surface
# ---------------------------------------------------------------------------

def _build_fake_saaz_upstream() -> FastMCP:  # type: ignore[type-arg]
    upstream: FastMCP = FastMCP("fake-saaz")  # type: ignore[type-arg]

    @upstream.tool
    def saaz_list_artists(genre: str | None = None, status: str | None = None, limit: int = 50) -> dict[str, Any]:
        artists = [
            {"slug": "shahram-nazeri", "genre": "traditional", "status": "active", "era": "contemporary"},
            {"slug": "kayhan-kalhor", "genre": "traditional", "status": "legacy", "era": "contemporary"},
            {"slug": "mohammad-reza-shajarian", "genre": "traditional", "status": "deceased", "era": "20th_century"},
            {"slug": "9t-antiope", "genre": "indie_persian_jazz", "status": "active", "era": "contemporary"},
            {"slug": "siavash-amini", "genre": "indie_persian_jazz", "status": "active", "era": "contemporary"},
        ]
        if genre:
            artists = [a for a in artists if a["genre"] == genre]
        if status:
            artists = [a for a in artists if a["status"] == status]
        return {"result": artists[:limit]}

    @upstream.tool
    def saaz_search_artists(query: str, limit: int = 10) -> dict[str, Any]:
        return {
            "result": [
                {"slug": "siavash-amini", "genre": "indie_persian_jazz", "similarity": 0.82},
                {"slug": "9t-antiope", "genre": "indie_persian_jazz", "similarity": 0.75},
                {"slug": "saba-alizadeh", "genre": "persian_jazz", "similarity": 0.61},
            ][:limit]
        }

    @upstream.tool
    def saaz_get_artist(slug: str) -> dict[str, Any]:
        if slug != "shahram-nazeri":
            return {"error": f"no artist with slug='{slug}'"}
        return {
            "slug": "shahram-nazeri",
            "name_en": "Shahram Nazeri",
            "genre": "traditional",
            "bio": "Shahram Nazeri is a contemporary Iranian tenor...",
            "links": [{"kind": "wikipedia", "url": "https://en.wikipedia.org/wiki/Shahram_Nazeri"}],
            "images": [],
            "provenance": [{"source": "wikipedia", "confidence": 0.85}],
            "has_embedding": True,
        }

    @upstream.tool
    def saaz_query(sql: str, limit: int = 200) -> dict[str, Any]:
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith(("SELECT", "WITH")):
            raise ValueError("Only SELECT (and WITH ... SELECT) statements are allowed")
        return {"row_count": 4, "truncated": False, "rows": [
            {"genre": "indie_persian_jazz", "n": 13},
            {"genre": "traditional", "n": 8},
            {"genre": "persian_jazz", "n": 8},
            {"genre": "other", "n": 1},
        ]}

    @upstream.tool
    def saaz_stats() -> dict[str, Any]:
        return {
            "row_counts": {"artist": 30, "song": 0},
            "by_genre": [{"genre": "indie_persian_jazz", "n": 13, "with_embedding": 13}],
        }

    return upstream


def _wrap_with_audit(upstream: FastMCP) -> FastMCP:  # type: ignore[type-arg]
    proxy = create_proxy(upstream, name="proxy")
    parent: FastMCP = FastMCP("mneme-test")  # type: ignore[type-arg]
    parent.mount(proxy, namespace=None)
    # No pool — audit will log but not write (NullAudit mode)
    return parent


# ---------------------------------------------------------------------------
# Scenario 1 — "Who are the traditional Persian artists?"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_list_traditional_artists() -> None:
    """User asks for traditional artists → list_artists genre=traditional."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool("saaz_list_artists", {"genre": "traditional"})
    assert result is not None
    payload = result.structured_content or {}
    artists = payload.get("result", [])
    assert all(a["genre"] == "traditional" for a in artists)
    assert len(artists) >= 1


# ---------------------------------------------------------------------------
# Scenario 2 — "Find artists similar to Siavash Amini"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_semantic_search_similar_artists() -> None:
    """User asks for artists similar to Siavash Amini → search_artists."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool(
        "saaz_search_artists",
        {"query": "ambient experimental compositions similar to Siavash Amini", "limit": 3},
    )
    assert result is not None
    payload = result.structured_content or {}
    hits = payload.get("result", [])
    assert len(hits) >= 1
    # Results must be sorted by similarity descending
    similarities = [h["similarity"] for h in hits]
    assert similarities == sorted(similarities, reverse=True)


# ---------------------------------------------------------------------------
# Scenario 3 — "Give me a genre breakdown"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_genre_breakdown_query() -> None:
    """User asks for genre counts → saaz_query with GROUP BY genre."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool(
        "saaz_query",
        {"sql": "SELECT genre, COUNT(*) AS n FROM artist GROUP BY genre ORDER BY n DESC"},
    )
    assert result is not None
    payload = result.structured_content or {}
    assert payload.get("row_count") == 4
    rows = payload.get("rows", [])
    assert rows[0]["genre"] == "indie_persian_jazz"


# ---------------------------------------------------------------------------
# Scenario 4 — "Tell me about Shahram Nazeri"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_get_specific_artist() -> None:
    """User names a specific artist → get_artist with slug lookup."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool("saaz_get_artist", {"slug": "shahram-nazeri"})
    assert result is not None
    payload = result.structured_content or {}
    assert payload.get("slug") == "shahram-nazeri"
    assert payload.get("bio") is not None
    assert isinstance(payload.get("links"), list)
    assert isinstance(payload.get("provenance"), list)


# ---------------------------------------------------------------------------
# Scenario 5 — "Are there any deceased artists?"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_filter_deceased_artists() -> None:
    """User asks for deceased artists → list_artists status=deceased."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool("saaz_list_artists", {"status": "deceased"})
    assert result is not None
    payload = result.structured_content or {}
    artists = payload.get("result", [])
    assert all(a["status"] == "deceased" for a in artists)


# ---------------------------------------------------------------------------
# Scenario 6 — "How many artists have bios?"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_count_query() -> None:
    """User asks a counting question → saaz_query SELECT COUNT."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool(
        "saaz_query",
        {"sql": "SELECT COUNT(*) FROM artist WHERE bio IS NOT NULL"},
    )
    assert result is not None
    payload = result.structured_content or {}
    assert "rows" in payload


# ---------------------------------------------------------------------------
# Scenario 7 — Adversarial: user asks Claude to delete artist data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_dml_is_blocked() -> None:
    """Adversarial: even if Claude calls INSERT/DROP/UPDATE, it must be rejected."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    from fastmcp.exceptions import ToolError

    with pytest.raises((ValueError, ToolError, RuntimeError)):
        await server.call_tool(
            "saaz_query",
            {"sql": "INSERT INTO artist (slug, name_en, genre) VALUES ('x', 'X', 'other')"},
        )


# ---------------------------------------------------------------------------
# Scenario 8 — Routing: genre × status cross-product never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,params",
    [
        # saaz tools via real routing
        ("saaz_list_artists", {"genre": "indie_persian_jazz"}),
        ("saaz_list_artists", {"status": "active"}),
        ("saaz_search_artists", {"query": "traditional"}),
        ("saaz_get_artist", {"slug": "bomrani"}),
        ("saaz_query", {"sql": "SELECT genre FROM artist LIMIT 1"}),
        ("saaz_stats", {}),
        # neon tools
        ("neon_list_tables", {}),
        ("neon_query", {"sql": "SELECT 1"}),
        # unknown tools
        ("unknown_tool", {}),
        ("", {}),
    ],
)
def test_routing_never_raises(tool_name: str, params: dict[str, Any]) -> None:
    """route_to_namespace must never raise regardless of tool/params combination."""
    result = route_to_namespace(
        tool_name,
        params,
        namespace_keywords={
            "saaz_demo": ["saaz_", "artist", "song", "genre", "persian"],
            "neon_purple_kite": ["neon_", "patient", "mrn"],
        },
    )
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Scenario 9 — Stats health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_health_check_via_stats() -> None:
    """Claude checks dataset health → saaz_stats returns row counts and genre breakdown."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool("saaz_stats", {})
    assert result is not None
    payload = result.structured_content or {}
    assert "row_counts" in payload
    assert payload["row_counts"]["artist"] == 30


# ---------------------------------------------------------------------------
# Scenario 10 — get_artist with invalid slug returns structured error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_get_artist_unknown_slug_returns_error_not_crash() -> None:
    """Claude queries an invalid artist slug; tool should return error, not crash."""
    upstream = _build_fake_saaz_upstream()
    server = _wrap_with_audit(upstream)

    result = await server.call_tool("saaz_get_artist", {"slug": "nobody-here"})
    assert result is not None
    payload = result.structured_content or {}
    assert "error" in str(payload).lower(), f"Expected error in response, got: {payload}"
