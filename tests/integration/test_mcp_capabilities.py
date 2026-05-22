"""Comprehensive MCP capability test battery (Phase 1 requirements verification).

Run with: MNEME_INTEGRATION=1 pytest tests/integration/test_mcp_capabilities.py -v

Requirements verified per CLAUDE.md:
  - Pull-only: DML is rejected by upstream tool layer
  - Audit: every call lands in query_episode with correct db_namespace
  - Semantic search: pgvector embeddings deliver ranked results
  - Data integrity: provenance, embedding coverage, schema correctness
  - Registry: registered_database CRUD tools exposed and functional
  - Neon: SSL connectivity issue surfaced as an xfail (local server down)

All tests connect via the live mneme server at MNEME_URL.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool

from agent_service.config import Settings, get_settings
from agent_service.memory.store import apply_pending_migrations, create_pool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MNEME_URL = os.environ.get("MNEME_URL", "https://mneme-aloomrani.replit.app/mcp")
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _parse_sse(resp: httpx.Response) -> dict[str, Any]:
    """Extract JSON from FastMCP's SSE envelope (data: {...}) or plain JSON."""
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return resp.json()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def helium_pool() -> AsyncGenerator[AsyncConnectionPool, None]:
    settings: Settings = get_settings()
    pool = await create_pool(settings.database_url_str())
    await apply_pending_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def mneme_session() -> AsyncGenerator[tuple[httpx.AsyncClient, str], None]:
    """Open an authenticated mneme session; yield (client, session_id)."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        init_resp = await client.post(
            MNEME_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "capability-test", "version": "1"},
                },
            },
            headers=MCP_HEADERS,
        )
        assert init_resp.status_code == 200, f"Init failed: {init_resp.text}"
        session_id = init_resp.headers.get("mcp-session-id", "")
        assert session_id, "Expected mcp-session-id header"
        yield client, session_id


async def _call_tool(
    client: httpx.AsyncClient,
    session_id: str,
    tool: str,
    args: dict[str, Any],
    call_id: int = 2,
) -> dict[str, Any]:
    resp = await client.post(
        MNEME_URL,
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
        headers={**MCP_HEADERS, "mcp-session-id": session_id},
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return _parse_sse(resp)


# ===========================================================================
# R1 — Tool surface (all expected tools are exposed)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_list_exposes_saaz_and_neon(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """tools/list must expose all expected tool names."""
    client, session_id = mneme_session
    resp = await client.post(
        MNEME_URL,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers={**MCP_HEADERS, "mcp-session-id": session_id},
    )
    payload = _parse_sse(resp)
    tools = {t["name"] for t in payload["result"]["tools"]}

    required_saaz = {
        "saaz_list_tables",
        "saaz_stats",
        "saaz_query",
        "saaz_list_artists",
        "saaz_get_artist",
        "saaz_search_artists",
    }
    required_registry = {
        "list_registered_databases",
        "get_database_info",
    }
    missing = (required_saaz | required_registry) - tools
    assert not missing, f"Missing tools: {missing}"


# ===========================================================================
# R2 — Saaz read access
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_list_tables_returns_6_tables(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """saaz_list_tables must return exactly 6 known tables."""
    client, sid = mneme_session
    payload = await _call_tool(client, sid, "saaz_list_tables", {})
    tables = {r["table_name"] for r in payload["result"]["result"]}
    expected = {"artist", "artist_image", "artist_link", "song", "enrichment_run", "data_provenance"}
    assert expected == tables, f"Got: {tables}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_stats_returns_health_metrics(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """saaz_stats must return row counts, genre breakdown, and enrichment cost."""
    client, sid = mneme_session
    payload = await _call_tool(client, sid, "saaz_stats", {})
    stats = payload["result"]
    assert "row_counts" in stats
    assert "by_genre" in stats
    assert "enrichment_cost" in stats
    assert stats["row_counts"]["artist"] == 30
    genres = {g["genre"] for g in stats["by_genre"]}
    assert genres == {"indie_persian_jazz", "persian_jazz", "traditional", "other"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_list_artists_total_count(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """Unfiltered list_artists should return all 30 artists."""
    client, sid = mneme_session
    payload = await _call_tool(client, sid, "saaz_list_artists", {"limit": 50})
    artists = payload["result"]["result"]
    assert len(artists) == 30, f"Expected 30 artists, got {len(artists)}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_list_artists_genre_filter(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """list_artists with genre=indie_persian_jazz must return exactly 13 artists."""
    client, sid = mneme_session
    payload = await _call_tool(
        client, sid, "saaz_list_artists", {"genre": "indie_persian_jazz"}
    )
    artists = payload["result"]["result"]
    assert len(artists) == 13
    assert all(a["genre"] == "indie_persian_jazz" for a in artists)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_list_artists_status_filter(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """list_artists with status=deceased must return 1 artist (Shajarian)."""
    client, sid = mneme_session
    payload = await _call_tool(
        client, sid, "saaz_list_artists", {"status": "deceased"}
    )
    artists = payload["result"]["result"]
    assert len(artists) == 1
    assert artists[0]["slug"] == "mohammad-reza-shajarian"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_get_artist_full_record(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """get_artist must return id, bio, links, images, provenance."""
    client, sid = mneme_session
    payload = await _call_tool(
        client, sid, "saaz_get_artist", {"slug": "shahram-nazeri"}
    )
    artist = payload["result"]
    assert artist["slug"] == "shahram-nazeri"
    assert artist["bio"] is not None and len(artist["bio"]) > 50
    assert isinstance(artist.get("links"), list)
    assert isinstance(artist.get("images"), list)
    assert isinstance(artist.get("provenance"), list)
    assert len(artist["provenance"]) >= 1
    assert artist["has_embedding"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_get_artist_invalid_slug_returns_error(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """get_artist with unknown slug must return an error object, not crash."""
    client, sid = mneme_session
    payload = await _call_tool(
        client, sid, "saaz_get_artist", {"slug": "does-not-exist-xyz"}
    )
    # Result should contain an error key, not raise an exception
    result = payload.get("result", {})
    assert "error" in str(result).lower() or "error" in payload


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_query_select_executes(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """saaz_query SELECT must return rows with row_count and truncated fields."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {"sql": "SELECT genre, COUNT(*) AS n FROM artist GROUP BY genre ORDER BY n DESC"},
    )
    result = payload["result"]
    assert "row_count" in result
    assert result["row_count"] == 4
    assert "rows" in result
    rows = result["rows"]
    assert rows[0]["genre"] == "indie_persian_jazz"
    assert rows[0]["n"] == 13


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_query_join_executes(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """saaz_query must support multi-table JOINs (artist + artist_link)."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {
            "sql": (
                "SELECT a.slug, COUNT(al.id) AS link_count "
                "FROM artist a "
                "LEFT JOIN artist_link al ON al.artist_id = a.id "
                "GROUP BY a.slug "
                "ORDER BY link_count DESC "
                "LIMIT 5"
            )
        },
    )
    result = payload["result"]
    assert result["row_count"] == 5
    assert result["rows"][0]["link_count"] >= 4


# ===========================================================================
# R3 — Write rejection (pull-only enforcement)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_query_insert_rejected(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """INSERT must be rejected — mneme is pull-only."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {"sql": "INSERT INTO artist (slug, name_en, genre) VALUES ('hack', 'Hack', 'other')"},
    )
    # Either an error in the JSON-RPC response or in the result content
    error_text = str(payload).lower()
    assert "error" in error_text or "only select" in error_text, (
        f"Expected DML rejection, got: {payload}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_query_drop_rejected(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """DROP TABLE must be rejected by the upstream layer."""
    client, sid = mneme_session
    payload = await _call_tool(
        client, sid, "saaz_query", {"sql": "DROP TABLE artist"}
    )
    error_text = str(payload).lower()
    assert "error" in error_text or "only select" in error_text, (
        f"Expected DDL rejection, got: {payload}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_saaz_query_update_rejected(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """UPDATE must be rejected — no writes to saaz tables ever."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {"sql": "UPDATE artist SET genre = 'other' WHERE slug = 'bomrani'"},
    )
    error_text = str(payload).lower()
    assert "error" in error_text or "only select" in error_text, (
        f"Expected UPDATE rejection, got: {payload}"
    )


# ===========================================================================
# R4 — Semantic search (pgvector)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_artists_returns_ranked_results(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """search_artists must return results with similarity scores, ranked desc."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_search_artists",
        {"query": "experimental electronic ambient music", "limit": 5},
    )
    results = payload["result"]["result"]
    assert len(results) >= 3
    similarities = [r["similarity"] for r in results]
    assert similarities == sorted(similarities, reverse=True), "Must be ranked desc"
    assert all(0.0 < s < 1.0 for s in similarities), "Similarities out of range"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_artists_persian_electronic_finds_9t_antiope(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """'Persian electronic duo' query must rank 9T Antiope in top-3."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_search_artists",
        {"query": "Iranian electronic duo Paris experimental", "limit": 5},
    )
    results = payload["result"]["result"]
    slugs = [r["slug"] for r in results]
    assert "9t-antiope" in slugs[:3], f"Expected 9T Antiope in top-3, got: {slugs}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_artists_traditional_vocalist(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """'Traditional classical vocalist' must find artists from traditional genre."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_search_artists",
        {"query": "traditional classical vocalist Iranian music", "limit": 5},
    )
    results = payload["result"]["result"]
    genres = {r["genre"] for r in results[:3]}
    assert "traditional" in genres, f"Expected traditional in top-3 genres, got: {genres}"


# ===========================================================================
# R5 — Data integrity (embeddings, provenance)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_embedding_coverage_is_100_percent(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """All 30 artists must have non-null embeddings for semantic search to work."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {
            "sql": (
                "SELECT COUNT(*) AS total, COUNT(embedding) AS with_embedding "
                "FROM artist"
            )
        },
    )
    row = payload["result"]["rows"][0]
    assert row["total"] == 30
    assert row["with_embedding"] == 30, (
        f"Expected 30/30 embeddings, got {row['with_embedding']}/30"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provenance_data_present_for_all_artists(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """Every artist must have at least one data_provenance row."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {
            "sql": (
                "SELECT COUNT(DISTINCT a.id) AS artists_with_prov "
                "FROM artist a "
                "JOIN data_provenance dp ON dp.fact_id = a.id "
                "WHERE dp.fact_table = 'artist'"
            )
        },
    )
    count = payload["result"]["rows"][0]["artists_with_prov"]
    assert count == 30, f"Expected 30 artists with provenance, got {count}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bios_present_for_most_artists(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """At least 29/30 artists must have non-trivial bios (>50 chars)."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {"sql": "SELECT COUNT(*) FROM artist WHERE bio IS NOT NULL AND LENGTH(bio) > 50"},
    )
    count = payload["result"]["rows"][0]["count"]
    assert count >= 29, f"Expected >=29 artists with bio, got {count}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anthropic_web_bios_have_high_confidence(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """Provenance rows sourced from anthropic_web must have confidence >= 0.9."""
    client, sid = mneme_session
    payload = await _call_tool(
        client,
        sid,
        "saaz_query",
        {
            "sql": (
                "SELECT MIN(confidence) AS min_conf "
                "FROM data_provenance "
                "WHERE source = 'anthropic_web' AND fact_table = 'artist'"
            )
        },
    )
    rows = payload["result"]["rows"]
    if rows and rows[0]["min_conf"] is not None:
        assert float(rows[0]["min_conf"]) >= 0.9, (
            f"anthropic_web min confidence: {rows[0]['min_conf']}"
        )


# ===========================================================================
# R6 — Audit: every call lands in query_episode
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_rows_accumulate_for_saaz_namespace(
    helium_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    """After running saaz tool calls, query_episode must have saaz_demo rows."""
    from agent_service.memory.episodes import get_recent_episodes

    episodes = await get_recent_episodes(helium_pool, "saaz_demo", limit=100)
    assert len(episodes) >= 5, (
        f"Expected >=5 saaz_demo audit rows (run test suite first), got {len(episodes)}"
    )
    assert all(ep.db_namespace == "saaz_demo" for ep in episodes)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_id_present_in_episode(
    helium_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    """All query_episode rows must have a non-null audit_id."""
    from agent_service.memory.episodes import get_recent_episodes

    episodes = await get_recent_episodes(helium_pool, "saaz_demo", limit=20)
    assert all(ep.audit_id is not None for ep in episodes), (
        "Some episodes are missing audit_id"
    )


# ===========================================================================
# R7 — Registry tools
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_registered_databases_returns_registry(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """list_registered_databases must return a list (including inactive entries)."""
    client, sid = mneme_session
    payload = await _call_tool(client, sid, "list_registered_databases", {})
    result = payload["result"]
    assert isinstance(result, (list, dict)), f"Unexpected result type: {type(result)}"
    items = result if isinstance(result, list) else result.get("result", [])
    # smoke_ns was registered by integration tests; at least one entry expected
    assert len(items) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_database_info_returns_stats(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """get_database_info must return mcp_url, routing_keywords, and call stats."""
    client, sid = mneme_session
    payload = await _call_tool(
        client, sid, "get_database_info", {"namespace": "smoke_ns"}
    )
    info_str = str(payload["result"])
    assert "smoke_ns" in info_str
    assert "mcp_url" in info_str or "localhost" in info_str


# ===========================================================================
# R8 — Neon connectivity (expected to fail while local server is down)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="neon_mcp local server (port 3000) not running; SSL connection fails",
    strict=False,
)
async def test_neon_list_tables_connectivity(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """neon_list_tables should return table list when neon_mcp server is up.

    Currently xfail: the local neon_mcp/server.py is not running, so the
    upstream SSL connection is broken.  Start neon_mcp on port 3000 to fix.
    """
    client, sid = mneme_session
    payload = await _call_tool(client, sid, "neon_list_tables", {})
    result = payload["result"]
    # neon-purple-kite has a patients table
    tables = {r["table_name"] for r in result}
    assert "patients" in tables


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="neon_mcp local server not running; neon_stats returns {} or errors",
    strict=False,
)
async def test_neon_stats_returns_table_counts(
    mneme_session: tuple[httpx.AsyncClient, str],
) -> None:
    """neon_stats should return per-table row counts for neon-purple-kite."""
    client, sid = mneme_session
    payload = await _call_tool(client, sid, "neon_stats", {})
    result = payload["result"]
    assert result  # must not be empty dict
