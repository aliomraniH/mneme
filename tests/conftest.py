"""Shared pytest fixtures.

Two tiers:
  Unit (default):    pytest-postgresql spins a local postgres process,
                     runs both migrations, truncates between tests.
  Integration:       @pytest.mark.integration, skipped unless MNEME_INTEGRATION=1
                     or `make test-integration`. Hits real Helium + live saaz upstream.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
from pytest_postgresql.factories import postgresql_proc

# Session-scoped process: one postgres server for the whole test run.
postgresql_my_proc = postgresql_proc()


# ---------------------------------------------------------------------------
# Unit-tier pool fixture (session-scoped — shares one PG process)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def unit_pool(
    postgresql_my_proc,  # type: ignore[no-untyped-def]
) -> AsyncGenerator[AsyncConnectionPool, None]:  # type: ignore[type-arg]
    """Session-scoped pool pointing at the local pytest-postgresql database."""
    proc = postgresql_my_proc
    # Build a libpq connection string from the process attributes
    password_part = f"password={proc.password} " if proc.password else ""
    conninfo = (
        f"host={proc.host} port={proc.port} "
        f"user={proc.user} dbname={proc.dbname} "
        f"{password_part}"
    )

    pool: AsyncConnectionPool = AsyncConnectionPool(  # type: ignore[type-arg]
        conninfo=conninfo,
        min_size=1,
        max_size=5,
        open=False,
    )
    await pool.open()

    # Run both migrations against the local test database
    mdir = Path(__file__).parent.parent / "migrations"
    async with pool.connection() as conn:
        await conn.execute((mdir / "0001_init.sql").read_text())
        await conn.execute((mdir / "0002_sessions.sql").read_text())
        await conn.commit()

    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def truncate_mneme_tables(
    unit_pool: AsyncConnectionPool,  # type: ignore[type-arg]
) -> AsyncGenerator[None, None]:
    """Truncate all mneme-owned tables after each test.

    Tests that touch the DB should request this fixture explicitly, or their
    module should declare:
        pytestmark = pytest.mark.usefixtures("truncate_mneme_tables")
    """
    yield
    async with unit_pool.connection() as conn:
        await conn.execute(
            """
            TRUNCATE
                mcp_session,
                query_episode,
                expertise_note,
                cache_event,
                db_schema_snapshot,
                column_doc
            CASCADE
            """
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Integration-tier: skip unless MNEME_INTEGRATION=1
# ---------------------------------------------------------------------------
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require real Helium Postgres and live saaz upstream",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("MNEME_INTEGRATION") == "1":
        return
    skip_integration = pytest.mark.skip(
        reason="set MNEME_INTEGRATION=1 to run integration tests"
    )
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_integration)
