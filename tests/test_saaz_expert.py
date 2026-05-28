"""Unit tests for Phase 2.5 SaazMusicExpert domain advisor.

Uses the shared Helium DB fixture (skipped without DATABASE_URL).
Isolates each test with TRUNCATE so tests don't bleed.
"""

from __future__ import annotations

import os
import pytest
import pytest_asyncio
from datetime import UTC, datetime, timedelta

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL"
)

# ── fixtures ──────────────────────────────────────────────────────────────────

import psycopg_pool

DB_URL = os.environ.get("DATABASE_URL", "")
NS = "saaz_demo"


@pytest_asyncio.fixture
async def pool():
    p = psycopg_pool.AsyncConnectionPool(DB_URL, open=False)
    await p.open()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE db_schema_snapshot, query_episode, cache_event RESTART IDENTITY CASCADE")
        await conn.commit()
    yield p
    await p.close()


async def _insert_snapshot(pool, tables: list[dict], age_hours: float = 0.5):
    """Insert a db_schema_snapshot row with a controlled captured_at."""
    import hashlib, json
    from psycopg.types.json import Json
    schema_hash = hashlib.sha256(json.dumps(tables, sort_keys=True).encode()).hexdigest()
    captured_at = datetime.now(UTC) - timedelta(hours=age_hours)
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO db_schema_snapshot (db_namespace, tables, schema_hash, source, captured_at)
            VALUES (%s, %s::jsonb, %s, 'manual', %s)
            """,
            (NS, Json(tables), schema_hash, captured_at),  # source must be 'introspect'|'manual'
        )
        await conn.commit()


async def _insert_episode(pool, tool_name: str, error: str | None = None, count: int = 1):
    """Insert query_episode rows for the given tool."""
    source = "error" if error else "ok"
    async with pool.connection() as conn:
        for _ in range(count):
            await conn.execute(
                """
                INSERT INTO query_episode
                    (tool_name, db_namespace, tool_params, source, row_count,
                     duration_ms, audit_id, error)
                VALUES (%s, %s, '{}'::jsonb, %s, 1, 10, gen_random_uuid(), %s)
                """,
                (tool_name, NS, source, error),
            )
        await conn.commit()


# ── tests ─────────────────────────────────────────────────────────────────────

from agent_service.advisors.saaz_expert import SaazMusicExpert


@pytest.mark.asyncio
async def test_no_snapshot_emits_stale_schema(pool):
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    assert len(advisories) == 1
    a = advisories[0]
    assert a.kind == "domain_expert"
    assert a.metadata["check"] == "stale_schema"
    assert a.confidence >= 0.85


@pytest.mark.asyncio
async def test_fresh_complete_snapshot_no_advisory(pool):
    tables = [{"name": n, "columns": []} for n in
               ("artist", "artist_image", "artist_link",
                "data_provenance", "enrichment_run", "song")]
    await _insert_snapshot(pool, tables, age_hours=0.5)
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    # No stale, no missing tables, no embedding concern (no episodes)
    assert advisories == []


@pytest.mark.asyncio
async def test_missing_table_emits_completeness_advisory(pool):
    # Snapshot missing "song" and "enrichment_run"
    tables = [{"name": n, "columns": []} for n in
               ("artist", "artist_image", "artist_link", "data_provenance")]
    await _insert_snapshot(pool, tables, age_hours=0.5)
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    kinds = {a.metadata["check"] for a in advisories}
    assert "schema_completeness" in kinds
    completeness = next(a for a in advisories if a.metadata["check"] == "schema_completeness")
    assert "song" in completeness.metadata["missing_tables"]
    assert completeness.confidence >= 0.9


@pytest.mark.asyncio
async def test_old_snapshot_emits_stale_advisory(pool):
    tables = [{"name": n, "columns": []} for n in
               ("artist", "artist_image", "artist_link",
                "data_provenance", "enrichment_run", "song")]
    await _insert_snapshot(pool, tables, age_hours=25)
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    checks = {a.metadata["check"] for a in advisories}
    assert "stale_schema" in checks
    stale = next(a for a in advisories if a.metadata["check"] == "stale_schema")
    assert stale.metadata["age_hours"] >= 24


@pytest.mark.asyncio
async def test_low_table_count_emits_provenance_gap(pool):
    # Only 2 tables in snapshot (saaz upstream cold-starting)
    tables = [{"name": "artist", "columns": []}, {"name": "song", "columns": []}]
    await _insert_snapshot(pool, tables, age_hours=0.5)
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    checks = {a.metadata["check"] for a in advisories}
    assert "provenance_gap" in checks
    assert "schema_completeness" in checks  # missing tables too


@pytest.mark.asyncio
async def test_no_search_calls_with_many_list_calls_warns(pool):
    tables = [{"name": n, "columns": []} for n in
               ("artist", "artist_image", "artist_link",
                "data_provenance", "enrichment_run", "song")]
    await _insert_snapshot(pool, tables, age_hours=0.5)
    await _insert_episode(pool, "saaz_list_artists", count=5)
    # No saaz_search_artists calls → embedding concern
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    checks = {a.metadata["check"] for a in advisories}
    assert "embedding_coverage" in checks
    emb = next(a for a in advisories if a.metadata["check"] == "embedding_coverage")
    assert emb.metadata["list_calls_6h"] == 5
    assert emb.metadata["search_calls_6h"] == 0


@pytest.mark.asyncio
async def test_search_calls_present_no_embedding_warning(pool):
    tables = [{"name": n, "columns": []} for n in
               ("artist", "artist_image", "artist_link",
                "data_provenance", "enrichment_run", "song")]
    await _insert_snapshot(pool, tables, age_hours=0.5)
    await _insert_episode(pool, "saaz_list_artists", count=3)
    await _insert_episode(pool, "saaz_search_artists", count=2)
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    checks = {a.metadata["check"] for a in advisories}
    assert "embedding_coverage" not in checks


@pytest.mark.asyncio
async def test_query_error_spike_emits_advisory(pool):
    tables = [{"name": n, "columns": []} for n in
               ("artist", "artist_image", "artist_link",
                "data_provenance", "enrichment_run", "song")]
    await _insert_snapshot(pool, tables, age_hours=0.5)
    await _insert_episode(pool, "saaz_query", error="syntax error", count=4)
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, NS)
    checks = {a.metadata["check"] for a in advisories}
    assert "query_error_spike" in checks
    err = next(a for a in advisories if a.metadata["check"] == "query_error_spike")
    assert err.metadata["query_errors_6h"] == 4
    assert err.confidence > 0.6


@pytest.mark.asyncio
async def test_wrong_namespace_returns_empty(pool):
    expert = SaazMusicExpert()
    advisories = await expert.advise(pool, "neon_purple_kite")
    assert advisories == []


@pytest.mark.asyncio
async def test_expert_registry_contains_saaz(pool):
    from agent_service.advisors.expert_base import get_experts
    import agent_service.advisors.saaz_expert  # noqa: F401 ensure registered
    experts = get_experts("saaz_demo")
    assert len(experts) >= 1
    assert any(isinstance(e, SaazMusicExpert) for e in experts)


@pytest.mark.asyncio
async def test_unknown_namespace_registry_returns_empty(pool):
    from agent_service.advisors.expert_base import get_experts
    assert get_experts("nonexistent_ns") == []
