"""Phase 2 integration tests — require MNEME_INTEGRATION=1 and a live server.

Run with: MNEME_INTEGRATION=1 make test-integration

Verifies the four Phase 2 agent-owned tools via the live mneme server:
  1. Root endpoint reports phase="2"
  2. get_query_history returns audit rows for prior tool calls
  3. get_schema_summary returns null snapshot + note before refresh
  4. get_advisories returns empty list (no drift/cache data yet)
  5. refresh_schema introspects saaz and writes a snapshot
  6. get_schema_summary returns snapshot after refresh
  7. Two different schemas → get_advisories returns schema_drift advisory
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
from agent_service.memory.schema import get_latest_snapshot
from agent_service.memory.store import apply_pending_migrations, create_pool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def integration_settings() -> Settings:
    return get_settings()


@pytest_asyncio.fixture(scope="module")
async def helium_pool(
    integration_settings: Settings,
) -> AsyncGenerator[AsyncConnectionPool, None]:  # type: ignore[type-arg]
    pool = await create_pool(integration_settings.database_url_str())
    await apply_pending_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module")
async def mneme_session() -> AsyncGenerator[tuple[httpx.AsyncClient, str, str], None]:
    """Yield (client, mneme_url, session_id) for a live mneme server."""
    mneme_url = os.environ.get("MNEME_URL", "https://mneme-aloomrani.replit.app/mcp")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        init_resp = await client.post(
            mneme_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "phase2-test", "version": "1"},
                },
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert init_resp.status_code == 200
        session_id = init_resp.headers.get("mcp-session-id", "")
        yield client, mneme_url, session_id


async def _call_tool(
    client: httpx.AsyncClient,
    url: str,
    session_id: str,
    tool: str,
    args: dict[str, Any],
    call_id: int = 10,
) -> Any:
    """Call a tool via the live MCP server and return the parsed result."""
    resp = await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "mcp-session-id": session_id,
        },
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"

    payload = _parse_sse_json(resp)
    assert "error" not in payload, f"JSON-RPC error: {payload.get('error')}"

    content_blocks = payload.get("result", {}).get("content", [])
    if content_blocks:
        raw = content_blocks[0].get("text", "")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw

    structured = payload.get("result", {}).get("structuredContent")
    return structured


def _parse_sse_json(resp: httpx.Response) -> dict[str, Any]:
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_root_endpoint_reports_phase_2() -> None:
    mneme_base = os.environ.get(
        "MNEME_BASE_URL", "https://mneme-aloomrani.replit.app"
    )
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(f"{mneme_base}/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["phase"] == "2", f"Expected phase 2, got: {data.get('phase')}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_query_history_returns_list(
    mneme_session: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, url, session_id = mneme_session

    # First make a saaz tool call so there's at least one episode
    await _call_tool(client, url, session_id, "saaz_stats", {}, call_id=20)

    # Now call get_query_history
    result = await _call_tool(
        client, url, session_id,
        "get_query_history",
        {"namespace": "saaz", "limit": 5},
        call_id=21,
    )

    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    # At least the saaz_stats call we just made
    assert len(result) >= 1
    ep = result[0]
    assert "tool_name" in ep
    assert "ts" in ep
    assert "source" in ep


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_query_history_wraps_result_summary(
    mneme_session: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, url, session_id = mneme_session

    result = await _call_tool(
        client, url, session_id,
        "get_query_history",
        {"namespace": "saaz", "limit": 10},
        call_id=22,
    )
    assert isinstance(result, list)
    # Any episode with a non-null result_summary must be wrapped
    wrapped_episodes = [e for e in result if e.get("result_summary") is not None]
    for ep in wrapped_episodes:
        assert "<<<UNTRUSTED_DATA>>>" in ep["result_summary"], (
            f"result_summary not wrapped: {ep['result_summary'][:80]}"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_summary_no_snapshot_initially(
    mneme_session: tuple[httpx.AsyncClient, str, str],
    helium_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    client, url, session_id = mneme_session

    # Ensure no snapshot exists for this namespace
    async with helium_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM db_schema_snapshot WHERE db_namespace = 'saaz_integration_test'"
        )
        await conn.commit()

    result = await _call_tool(
        client, url, session_id,
        "get_schema_summary",
        {"db": "saaz_integration_test"},
        call_id=23,
    )

    assert isinstance(result, dict)
    assert result["snapshot"] is None
    assert "refresh_schema" in result["note"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_advisories_returns_list(
    mneme_session: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, url, session_id = mneme_session

    result = await _call_tool(
        client, url, session_id,
        "get_advisories",
        {"db": "saaz"},
        call_id=24,
    )

    assert isinstance(result, list), f"Expected list, got: {type(result)}"
    # May be empty (no drift/cache signals yet) — that's fine
    for advisory in result:
        assert "kind" in advisory
        assert "db_namespace" in advisory
        assert "message" in advisory
        assert "confidence" in advisory


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_schema_writes_snapshot(
    mneme_session: tuple[httpx.AsyncClient, str, str],
    helium_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    client, url, session_id = mneme_session

    result = await _call_tool(
        client, url, session_id,
        "refresh_schema",
        {"db": "saaz", "tool_prefix": "saaz"},
        call_id=25,
    )

    assert isinstance(result, dict), f"Expected dict, got: {type(result)}"
    assert "snapshot_id" in result
    assert result["table_count"] > 0, "Expected at least one table to be introspected"
    assert len(result["tables"]) == result["table_count"]

    # Verify the row landed in Helium
    snapshot = await get_latest_snapshot(helium_pool, "saaz")
    assert snapshot is not None
    assert snapshot["schema_hash"] is not None
    assert len(snapshot["tables"]) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_summary_after_refresh(
    mneme_session: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, url, session_id = mneme_session

    # refresh first (may already have been done in prior test)
    await _call_tool(
        client, url, session_id,
        "refresh_schema",
        {"db": "saaz", "tool_prefix": "saaz"},
        call_id=26,
    )

    summary = await _call_tool(
        client, url, session_id,
        "get_schema_summary",
        {"db": "saaz"},
        call_id=27,
    )

    assert isinstance(summary, dict)
    assert summary["snapshot"] is not None
    assert summary["snapshot"]["schema_hash"] is not None
    assert len(summary["snapshot"]["tables"]) > 0
    assert len(summary["history"]) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schema_drift_detected_after_two_different_snapshots(
    helium_pool: AsyncConnectionPool,  # type: ignore[type-arg]
    mneme_session: tuple[httpx.AsyncClient, str, str],
) -> None:
    """Insert two snapshots with different hashes → get_advisories returns schema_drift."""
    from agent_service.memory.schema import write_schema_snapshot

    ns = "drift_test_namespace"

    # Clear any prior snapshots for this namespace
    async with helium_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM db_schema_snapshot WHERE db_namespace = %s", (ns,)
        )
        await conn.commit()

    # Write two different snapshots directly to Helium
    await write_schema_snapshot(helium_pool, ns, [{"name": "v1", "columns": []}])
    await write_schema_snapshot(helium_pool, ns, [{"name": "v2", "columns": [{"name": "new_col"}]}])

    client, url, session_id = mneme_session
    result = await _call_tool(
        client, url, session_id,
        "get_advisories",
        {"db": ns},
        call_id=28,
    )

    assert isinstance(result, list)
    drift_advisories = [a for a in result if a["kind"] == "schema_drift"]
    assert len(drift_advisories) == 1, (
        f"Expected 1 schema_drift advisory, got: {result}"
    )
    assert drift_advisories[0]["db_namespace"] == ns
    assert drift_advisories[0]["confidence"] == 1.0

    # Cleanup
    async with helium_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM db_schema_snapshot WHERE db_namespace = %s", (ns,)
        )
        await conn.commit()
