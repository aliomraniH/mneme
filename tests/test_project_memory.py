"""DB-backed tests for project_memory + session_context_cache.

Require DATABASE_URL (Helium). Skipped otherwise, like the rest of the unit
tier. Use rule-based paths (query_vector=None) so no embedding API is needed.
"""

from __future__ import annotations

import pytest
from psycopg_pool import AsyncConnectionPool

from agent_service.memory.context_cache import get_latest_cache, write_cache_version
from agent_service.memory.project import (
    increment_call_counts,
    list_memory,
    search_memory,
    write_memory,
)

pytestmark = pytest.mark.usefixtures("truncate_mneme_tables")


# ---------------------------------------------------------------------------
# project_memory write / search (rule-based, no embeddings)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_memory_creates_row(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    result = await write_memory(
        unit_pool,
        project_id="proj_a",
        db_namespace="saaz",
        content="genre column is nullable",
        embedding=None,
        key_findings=["use COALESCE for display"],
        source="thread_summary",
    )
    assert result["action"] == "created"
    assert result["memory_id"]


@pytest.mark.asyncio
async def test_write_memory_dedup_by_content(unit_pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    """Identical content (no embedding) merges instead of duplicating."""
    first = await write_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        content="same fact", embedding=None,
    )
    second = await write_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        content="same fact", embedding=None,
    )
    assert first["action"] == "created"
    assert second["action"] == "merged_with_existing"
    assert first["memory_id"] == second["memory_id"]

    rows = await list_memory(unit_pool, "proj_a", "saaz")
    assert len(rows) == 1
    assert rows[0]["call_count"] == 1  # bumped once on merge


@pytest.mark.asyncio
async def test_search_memory_rule_based_orders_by_usage(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    await write_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        content="rarely used", embedding=None,
    )
    popular = await write_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        content="frequently used", embedding=None,
    )
    # Bump the popular one several times
    for _ in range(5):
        await increment_call_counts(unit_pool, [popular["memory_id"]])

    results = await search_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        query_vector=None, limit=10,
    )
    assert len(results) == 2
    assert results[0]["content"] == "frequently used"  # higher call_count ranks first


@pytest.mark.asyncio
async def test_search_memory_namespace_isolation(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    await write_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        content="saaz fact", embedding=None,
    )
    await write_memory(
        unit_pool, project_id="proj_a", db_namespace="neon",
        content="neon fact", embedding=None,
    )
    saaz = await search_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        query_vector=None, limit=10,
    )
    assert len(saaz) == 1
    assert saaz[0]["content"] == "saaz fact"


@pytest.mark.asyncio
async def test_search_memory_project_isolation(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    await write_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        content="A fact", embedding=None,
    )
    await write_memory(
        unit_pool, project_id="proj_b", db_namespace="saaz",
        content="B fact", embedding=None,
    )
    a = await search_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        query_vector=None, limit=10,
    )
    assert len(a) == 1
    assert a[0]["content"] == "A fact"


@pytest.mark.asyncio
async def test_search_memory_include_general(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    await write_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        content="project fact", embedding=None, scope="project",
    )
    await write_memory(
        unit_pool, project_id="other_proj", db_namespace="saaz",
        content="general fact", embedding=None, scope="general",
    )
    # Without include_general: only project fact
    without = await search_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        query_vector=None, include_general=False, limit=10,
    )
    assert {r["content"] for r in without} == {"project fact"}

    # With include_general: both
    with_general = await search_memory(
        unit_pool, project_id="proj_a", db_namespace="saaz",
        query_vector=None, include_general=True, limit=10,
    )
    assert {r["content"] for r in with_general} == {"project fact", "general fact"}


# ---------------------------------------------------------------------------
# session_context_cache versioning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_context_cache_first_version_is_one(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    v = await write_cache_version(
        unit_pool, session_id="sess1", project_id="proj_a",
        db_namespace="saaz", payload={"keys": []}, token_estimate=100,
    )
    assert v == 1


@pytest.mark.asyncio
async def test_context_cache_increments_version(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    await write_cache_version(
        unit_pool, session_id="sess1", project_id="proj_a",
        db_namespace="saaz", payload={"keys": ["a"]}, token_estimate=100,
    )
    v2 = await write_cache_version(
        unit_pool, session_id="sess1", project_id="proj_a",
        db_namespace="saaz", payload={"keys": ["b"]}, token_estimate=80,
    )
    assert v2 == 2

    latest = await get_latest_cache(unit_pool, "sess1")
    assert latest is not None
    assert latest["version"] == 2
    assert latest["payload"]["keys"] == ["b"]
    assert latest["token_estimate"] == 80


@pytest.mark.asyncio
async def test_context_cache_get_latest_none(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    assert await get_latest_cache(unit_pool, "nonexistent_session") is None


@pytest.mark.asyncio
async def test_context_cache_session_isolation(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> None:
    await write_cache_version(
        unit_pool, session_id="sessA", project_id="p",
        db_namespace="saaz", payload={"keys": ["x"]}, token_estimate=10,
    )
    await write_cache_version(
        unit_pool, session_id="sessB", project_id="p",
        db_namespace="saaz", payload={"keys": ["y"]}, token_estimate=20,
    )
    a = await get_latest_cache(unit_pool, "sessA")
    b = await get_latest_cache(unit_pool, "sessB")
    assert a is not None and a["payload"]["keys"] == ["x"]
    assert b is not None and b["payload"]["keys"] == ["y"]
    assert a["version"] == 1 and b["version"] == 1
