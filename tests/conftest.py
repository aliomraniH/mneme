"""Shared pytest fixtures.

Two tiers:
  Unit (default):    Uses $DATABASE_URL (Helium on Replit) with TRUNCATE isolation.
                     Runs both migrations on startup, truncates between tests.
  Integration:       @pytest.mark.integration, skipped unless MNEME_INTEGRATION=1
                     or `make test-integration`. Hits real Helium + live saaz upstream.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool

from agent_service.memory.store import apply_pending_migrations, create_pool


# ---------------------------------------------------------------------------
# Unit-tier pool fixture (uses $DATABASE_URL directly, isolated via TRUNCATE)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def unit_pool() -> AsyncGenerator[AsyncConnectionPool, None]:  # type: ignore[type-arg]
    """Session-scoped pool pointing at $DATABASE_URL (Helium on Replit).

    Applies both migrations on startup (idempotent). Tests truncate mneme tables
    between runs for isolation.
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not set — unit tests need Helium access")

    pool = await create_pool(database_url)

    # Apply both migrations (idempotent; 0001 was already applied by Replit Agent,
    # but we run it anyway to ensure the test DB is in sync)
    await apply_pending_migrations(pool)

    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def truncate_mneme_tables(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> AsyncGenerator[None, None]:
    """Truncate all mneme-owned tables before each test for clean isolation.

    Tests that touch the DB should request this fixture explicitly, or their
    module should declare:
        pytestmark = pytest.mark.usefixtures("truncate_mneme_tables")
    """
    _truncate_sql = """
        TRUNCATE
            mcp_session,
            query_episode,
            expertise_note,
            cache_event,
            db_schema_snapshot,
            column_doc,
            registered_database
        CASCADE
    """
    async with unit_pool.connection() as conn:
        await conn.execute(_truncate_sql)
        await conn.commit()
    yield
    async with unit_pool.connection() as conn:
        await conn.execute(_truncate_sql)
        await conn.commit()


# ---------------------------------------------------------------------------
# Integration-tier: skip unless MNEME_INTEGRATION=1
# ---------------------------------------------------------------------------
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require real Helium Postgres and live saaz upstream",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("MNEME_INTEGRATION") == "1":
        return
    skip_integration = pytest.mark.skip(reason="set MNEME_INTEGRATION=1 to run integration tests")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_integration)
