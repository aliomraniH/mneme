from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import structlog
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger(__name__)


async def write_schema_snapshot(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    db_namespace: str,
    tables: list[dict[str, Any]],
    source: str = "introspect",
) -> UUID:
    """Insert a new db_schema_snapshot row and return its id.

    Computes a deterministic SHA-256 hash of the tables JSON so that
    SchemaDriftAdvisor can detect changes by hash comparison alone.
    """
    schema_hash = hashlib.sha256(
        json.dumps(tables, sort_keys=True, default=str).encode()
    ).hexdigest()

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO db_schema_snapshot
                (db_namespace, tables, schema_hash, source)
            VALUES (%s, %s::jsonb, %s, %s)
            RETURNING id
            """,
            (db_namespace, Json(tables), schema_hash, source),
        )
        row = await cur.fetchone()
        await conn.commit()

    assert row is not None
    snapshot_id: UUID = row[0]
    log.info(
        "schema_snapshot_written",
        db_namespace=db_namespace,
        schema_hash=schema_hash[:12],
        table_count=len(tables),
    )
    return snapshot_id


async def get_latest_snapshot(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    db_namespace: str,
) -> dict[str, Any] | None:
    """Return the most recent schema snapshot for a namespace, or None."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, db_namespace, captured_at, tables, schema_hash, source
            FROM   db_schema_snapshot
            WHERE  db_namespace = %s
            ORDER  BY captured_at DESC
            LIMIT  1
            """,
            (db_namespace,),
        )
        row = await cur.fetchone()

    if row is None:
        return None

    return {
        "id": str(row[0]),
        "db_namespace": row[1],
        "captured_at": row[2].isoformat(),
        "tables": row[3],
        "schema_hash": row[4],
        "source": row[5],
    }


async def get_snapshot_history(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    db_namespace: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return recent schema snapshots (metadata only, no full tables blob)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, captured_at, schema_hash, source
            FROM   db_schema_snapshot
            WHERE  db_namespace = %s
            ORDER  BY captured_at DESC
            LIMIT  %s
            """,
            (db_namespace, limit),
        )
        rows = await cur.fetchall()

    return [
        {
            "id": str(row[0]),
            "captured_at": row[1].isoformat(),
            "schema_hash": row[2],
            "source": row[3],
        }
        for row in rows
    ]
