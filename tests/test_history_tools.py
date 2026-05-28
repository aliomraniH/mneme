"""Tests for Phase 2 history and schema tools."""

from __future__ import annotations

import pytest
from psycopg_pool import AsyncConnectionPool

from agent_service.memory.episodes import write_episode
from agent_service.memory.schema import (
    get_latest_snapshot,
    get_snapshot_history,
    write_schema_snapshot,
)
from agent_service.models import Episode

pytestmark = pytest.mark.usefixtures("truncate_mneme_tables")


# ---------------------------------------------------------------------------
# write_schema_snapshot / get_latest_snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_and_read_snapshot(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    tables = [{"name": "artist", "columns": [{"name": "id", "type": "uuid"}]}]
    snapshot_id = await write_schema_snapshot(unit_pool, "saaz", tables)
    assert snapshot_id is not None

    result = await get_latest_snapshot(unit_pool, "saaz")
    assert result is not None
    assert result["db_namespace"] == "saaz"
    assert result["tables"] == tables
    assert len(result["schema_hash"]) == 64  # SHA-256 hex
    assert result["source"] == "introspect"


@pytest.mark.asyncio
async def test_get_latest_snapshot_none(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    result = await get_latest_snapshot(unit_pool, "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_snapshot_hash_deterministic(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    tables = [{"name": "artist", "columns": []}]
    await write_schema_snapshot(unit_pool, "saaz", tables)
    await write_schema_snapshot(unit_pool, "saaz", tables)

    snap1 = await get_latest_snapshot(unit_pool, "saaz")
    assert snap1 is not None
    # Both rows have the same hash
    async with unit_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT DISTINCT schema_hash FROM db_schema_snapshot WHERE db_namespace = 'saaz'"
        )
        rows = await cur.fetchall()
    assert len(rows) == 1  # same hash for same content


@pytest.mark.asyncio
async def test_snapshot_history_ordering(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    for i in range(3):
        await write_schema_snapshot(
            unit_pool, "saaz", [{"name": f"table_{i}", "columns": []}]
        )

    history = await get_snapshot_history(unit_pool, "saaz", limit=5)
    assert len(history) == 3
    # Newest first
    assert history[0]["captured_at"] >= history[1]["captured_at"]
    assert history[1]["captured_at"] >= history[2]["captured_at"]
    # History rows do not include full tables blob
    assert "tables" not in history[0]


@pytest.mark.asyncio
async def test_snapshot_namespace_isolation(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    await write_schema_snapshot(unit_pool, "saaz", [{"name": "artist", "columns": []}])
    assert await get_latest_snapshot(unit_pool, "neon") is None


# ---------------------------------------------------------------------------
# get_query_history (via get_recent_episodes on the pool directly)
# We test the DB layer here; tool-level tests would need a running MCP server.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_history_filter_namespace(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    for ns in ("saaz", "neon", "saaz"):
        await write_episode(unit_pool, Episode(
            db_namespace=ns,
            tool_name="query",
            tool_params={},
        ))

    async with unit_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM query_episode WHERE db_namespace = 'saaz'"
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 2


@pytest.mark.asyncio
async def test_query_history_error_filter(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    await write_episode(unit_pool, Episode(
        db_namespace="saaz", tool_name="query", tool_params={}, source="ok"
    ))
    await write_episode(unit_pool, Episode(
        db_namespace="saaz", tool_name="query", tool_params={},
        error="syntax error", source="error"
    ))

    async with unit_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM query_episode WHERE db_namespace = 'saaz' AND source = 'error'"
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_query_history_duration_ms_recorded(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    await write_episode(unit_pool, Episode(
        db_namespace="saaz", tool_name="slow_query", tool_params={},
        duration_ms=1500, row_count=42,
    ))

    async with unit_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT duration_ms, row_count FROM query_episode WHERE tool_name = 'slow_query'"
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1500
    assert row[1] == 42
