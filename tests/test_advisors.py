"""Tests for the three Phase 2 advisor classes."""

from __future__ import annotations

import pytest
from psycopg_pool import AsyncConnectionPool

from agent_service.advisors.cache_stale import CacheStaleAdvisor
from agent_service.advisors.conflict import ConflictAdvisor
from agent_service.advisors.schema_drift import SchemaDriftAdvisor
from agent_service.memory.episodes import write_episode
from agent_service.memory.schema import write_schema_snapshot
from agent_service.models import Episode

pytestmark = pytest.mark.usefixtures("truncate_mneme_tables")


# ---------------------------------------------------------------------------
# SchemaDriftAdvisor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schema_drift_no_snapshots(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    advisories = await SchemaDriftAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


@pytest.mark.asyncio
async def test_schema_drift_one_snapshot(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    await write_schema_snapshot(unit_pool, "saaz", [{"name": "artist", "columns": []}])
    advisories = await SchemaDriftAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


@pytest.mark.asyncio
async def test_schema_drift_same_hash(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    tables = [{"name": "artist", "columns": [{"name": "id", "type": "uuid"}]}]
    await write_schema_snapshot(unit_pool, "saaz", tables)
    await write_schema_snapshot(unit_pool, "saaz", tables)
    advisories = await SchemaDriftAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


@pytest.mark.asyncio
async def test_schema_drift_different_hash(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    tables_v1 = [{"name": "artist", "columns": []}]
    tables_v2 = [{"name": "artist", "columns": [{"name": "bio", "type": "text"}]}]
    await write_schema_snapshot(unit_pool, "saaz", tables_v1)
    await write_schema_snapshot(unit_pool, "saaz", tables_v2)

    advisories = await SchemaDriftAdvisor().advise(unit_pool, "saaz")
    assert len(advisories) == 1
    a = advisories[0]
    assert a.kind == "schema_drift"
    assert a.db_namespace == "saaz"
    assert a.confidence == 1.0
    assert "new_hash" in a.metadata
    assert a.metadata["old_hash"] != a.metadata["new_hash"]


@pytest.mark.asyncio
async def test_schema_drift_namespace_isolation(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    """Drift in one namespace does not affect another."""
    await write_schema_snapshot(unit_pool, "saaz", [{"name": "a", "columns": []}])
    await write_schema_snapshot(unit_pool, "saaz", [{"name": "b", "columns": []}])
    # neon has no snapshots
    advisories = await SchemaDriftAdvisor().advise(unit_pool, "neon")
    assert advisories == []


# ---------------------------------------------------------------------------
# CacheStaleAdvisor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_stale_empty(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    advisories = await CacheStaleAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


@pytest.mark.asyncio
async def test_cache_stale_expired_entry(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    async with unit_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO cache_event (db_namespace, cache_key, written_at, ttl_seconds)
            VALUES ('saaz', 'artist_list', now() - interval '2 hours', 3600)
            """
        )
        await conn.commit()

    advisories = await CacheStaleAdvisor().advise(unit_pool, "saaz")
    assert len(advisories) == 1
    a = advisories[0]
    assert a.kind == "cache_stale"
    assert a.metadata["cache_key"] == "artist_list"
    assert a.confidence == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_cache_stale_fresh_entry(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    async with unit_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO cache_event (db_namespace, cache_key, written_at, ttl_seconds)
            VALUES ('saaz', 'fresh_key', now() - interval '10 minutes', 3600)
            """
        )
        await conn.commit()

    advisories = await CacheStaleAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


@pytest.mark.asyncio
async def test_cache_stale_already_invalidated(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    async with unit_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO cache_event
                (db_namespace, cache_key, written_at, ttl_seconds, invalidated_at, reason)
            VALUES ('saaz', 'old_key', now() - interval '2 hours', 3600, now(), 'manual')
            """
        )
        await conn.commit()

    advisories = await CacheStaleAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


# ---------------------------------------------------------------------------
# ConflictAdvisor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conflict_no_episodes(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    advisories = await ConflictAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


@pytest.mark.asyncio
async def test_conflict_stable_counts(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    for _ in range(3):
        await write_episode(unit_pool, Episode(
            db_namespace="saaz",
            tool_name="saaz_query",
            tool_params={},
            row_count=30,
        ))

    advisories = await ConflictAdvisor().advise(unit_pool, "saaz")
    assert advisories == []


@pytest.mark.asyncio
async def test_conflict_divergent_counts(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    for count in (100, 50):
        await write_episode(unit_pool, Episode(
            db_namespace="saaz",
            tool_name="saaz_query",
            tool_params={},
            row_count=count,
        ))

    advisories = await ConflictAdvisor(threshold=0.05).advise(unit_pool, "saaz")
    assert len(advisories) == 1
    a = advisories[0]
    assert a.kind == "potential_conflict"
    assert a.metadata["min_rows"] == 50
    assert a.metadata["max_rows"] == 100
    assert a.metadata["divergence_pct"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_conflict_below_threshold(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    for count in (100, 101):
        await write_episode(unit_pool, Episode(
            db_namespace="saaz",
            tool_name="saaz_query",
            tool_params={},
            row_count=count,
        ))

    advisories = await ConflictAdvisor(threshold=0.05).advise(unit_pool, "saaz")
    assert advisories == []
