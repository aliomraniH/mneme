from __future__ import annotations

import os
from pathlib import Path

from psycopg_pool import AsyncConnectionPool


async def create_pool(database_url: str) -> AsyncConnectionPool:  # type: ignore[type-arg]
    """Create and open the shared async connection pool.

    One pool per process. Callers must call pool.close() on shutdown.
    """
    pool: AsyncConnectionPool = AsyncConnectionPool(  # type: ignore[type-arg]
        conninfo=database_url,
        min_size=1,
        max_size=10,
        open=False,
    )
    await pool.open()
    return pool


async def run_migrations(pool: AsyncConnectionPool, *migration_paths: Path) -> None:  # type: ignore[type-arg]
    """Run SQL migration files in order against the shared pool.

    Uses IF NOT EXISTS / IF EXISTS guards so re-runs are idempotent.
    Never runs 0001_init.sql — that migration was applied by the Replit Agent
    before this server was deployed.
    """
    async with pool.connection() as conn:
        for path in migration_paths:
            sql = path.read_text()
            await conn.execute(sql)
        await conn.commit()


def migrations_dir() -> Path:
    here = Path(__file__).parent
    return here.parent.parent / "migrations"


async def apply_pending_migrations(pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    """Apply only 0002_sessions.sql (0001 is already applied on Helium)."""
    mdir = migrations_dir()
    pending = [mdir / "0002_sessions.sql"]
    existing = [p for p in pending if p.exists()]
    if existing:
        await run_migrations(pool, *existing)
