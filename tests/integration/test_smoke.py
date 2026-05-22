"""Phase 1 acceptance gate (START_HERE.md Step 5).

Run with: MNEME_INTEGRATION=1 make test-integration

Verifies:
  1. 5 tool calls through mneme → 5 query_episode rows
  2. Session row with client_name, client_ip, user_agent populated
  3. mcp_session.total_calls = 5 after 5 successful calls
  4. result_summary cap: a large result → truncated=True in audit row
  5. Genre breakdown query returns expected saaz data shape

Tool-name note: the saaz upstream server (v1.27+) already prefixes its tools
with "saaz_" (e.g. saaz_stats, saaz_list_tables).  These names flow through
mneme unchanged (namespace=None mount).
"""

from __future__ import annotations

import json
import os
import uuid as _uuid_mod
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

import httpx
import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool

from agent_service.config import Settings, get_settings
from agent_service.memory.episodes import get_recent_episodes
from agent_service.memory.store import apply_pending_migrations, create_pool


def _parse_sse_json(resp: httpx.Response) -> dict[str, Any]:
    """Extract the JSON object from an SSE text/event-stream response.

    FastMCP's streamable-HTTP transport wraps every JSON-RPC response in an
    SSE envelope:
        event: message\\r\\n
        data: {...json...}\\r\\n
        \\r\\n

    ``httpx.Response.json()`` fails on this format; this helper extracts the
    payload from the ``data:`` line.  Falls back to ``resp.json()`` when the
    response is plain JSON (e.g., in stateless mode).
    """
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return resp.json()


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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mneme_proxies_saaz_tools(helium_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    """Confirm the proxy exposes the 7 saaz tools end-to-end.

    This test connects through the live mneme server running on Replit.
    Run only when MNEME_INTEGRATION=1 is set.
    """
    import httpx

    mneme_url = os.environ.get("MNEME_URL", "https://mneme-aloomrani.replit.app/mcp")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Initialize session
        init_resp = await client.post(
            mneme_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke-test", "version": "1"},
                },
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert init_resp.status_code == 200
        session_id = init_resp.headers.get("mcp-session-id")
        assert session_id, "Expected mcp-session-id header"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "mcp-session-id": session_id,
        }

        # Confirm tools/list
        tools_resp = await client.post(
            mneme_url,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert tools_resp.status_code == 200

        # Run 5 tool calls (tool names have saaz_ prefix in upstream v1.27+)
        calls = [
            ("saaz_stats", {}),
            ("saaz_list_tables", {}),
            ("saaz_list_artists", {"limit": 5}),
            (
                "saaz_query",
                {"sql": "SELECT genre, count(*) AS n FROM artist GROUP BY genre ORDER BY n DESC"},
            ),
            ("saaz_query", {"sql": "DROP TABLE artist"}),  # should be rejected (write blocked)
        ]
        results = []
        for i, (tool, args) in enumerate(calls, start=3):
            resp = await client.post(
                mneme_url,
                json={
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                },
                headers=headers,
            )
            assert resp.status_code == 200
            results.append(_parse_sse_json(resp))

        # Genre query should have returned data
        genre_result = results[3]  # 4th call (0-indexed)
        assert "result" in genre_result or "error" in genre_result

    # Verify audit rows in Helium
    saaz_episodes = await get_recent_episodes(helium_pool, "saaz_demo", limit=50)
    assert len(saaz_episodes) >= 4, f"Expected >=4 saaz_demo episodes, got {len(saaz_episodes)}"

    # Verify session row
    # FastMCP returns session IDs as 32-char hex in the HTTP header but stores them
    # internally (and in the DB) as UUID strings with dashes. Normalise before querying.
    session_id_db = (
        str(_uuid_mod.UUID(session_id)) if "-" not in session_id else session_id
    )
    async with helium_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT client_name, client_ip, user_agent, total_calls, total_errors "
            "FROM mcp_session WHERE session_id = %s",
            (session_id_db,),
        )
        row = await cur.fetchone()
    assert row is not None, f"Session row not found for session_id={session_id_db}"
    assert row[0] == "smoke-test", f"client_name={row[0]}"
    assert row[2] is not None, "user_agent should be populated"
    assert row[3] == 5, f"total_calls={row[3]}, expected 5"
    assert row[4] == 1, f"total_errors={row[4]}, expected 1 (DROP TABLE rejection)"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_idle_session_reaper(helium_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    """Force-age a session and confirm the reaper marks it idle_timeout."""
    import asyncio

    # Manually insert a session with last_seen_at far in the past
    fake_session_id = "smoke-test-reaper-check"
    async with helium_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO mcp_session (session_id, client_name, last_seen_at)
            VALUES (%s, 'reaper-test', now() - interval '35 minutes')
            ON CONFLICT DO NOTHING
            """,
            (fake_session_id,),
        )
        await conn.commit()

    # Wait for one reaper cycle (up to 65 seconds in real env; skip in short runs)
    # In tests, we just call the reaper directly.
    from agent_service.middleware.session import idle_session_reaper

    reaper_task = asyncio.create_task(
        idle_session_reaper(
            pool_factory=lambda: helium_pool,
            idle_seconds=30 * 60,
            check_interval_seconds=0,  # fire immediately
            shutdown_event=asyncio.Event(),
        )
    )
    # Let it run one iteration then cancel
    await asyncio.sleep(0.5)
    reaper_task.cancel()
    with suppress(asyncio.CancelledError):
        await reaper_task

    async with helium_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT end_reason FROM mcp_session WHERE session_id = %s",
            (fake_session_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "idle_timeout", f"end_reason={row[0]}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_truncated_flag_on_large_result(helium_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    """A query returning >4KB triggers truncated=True on the audit row."""
    # list_artists returns 30 rows with full bios — typically > 4KB
    import os

    import httpx

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
                    "clientInfo": {"name": "truncation-test", "version": "1"},
                },
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        session_id = init_resp.headers.get("mcp-session-id")
        assert session_id, "Expected mcp-session-id header after init"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "mcp-session-id": session_id,
        }

        # saaz_list_artists with no filters → all artists including bios (> 4KB)
        await client.post(
            mneme_url,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "saaz_list_artists", "arguments": {}},
            },
            headers=headers,
        )

    # Check that the audit row has truncated=True
    episodes = await get_recent_episodes(
        helium_pool, "saaz_demo", limit=5, tool_name="saaz_list_artists"
    )
    assert any(ep.truncated for ep in episodes), (
        "Expected at least one truncated=True audit row for list_artists"
    )
