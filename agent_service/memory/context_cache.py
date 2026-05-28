"""session_context_cache CRUD — versioned record of injected context.

warm_up writes version 1. Each thread_refresh writes version N+1 carrying the
retained keys plus the newly added entries, so mneme always knows exactly what
is currently live in the session's context window and can compute drop/retain
diffs against the conversation's new focus.
"""

from __future__ import annotations

from typing import Any

import structlog
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger(__name__)


async def get_latest_cache(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    session_id: str,
) -> dict[str, Any] | None:
    """Return the most recent context-cache version for a session, or None."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT version, project_id, db_namespace, payload, token_estimate
            FROM   session_context_cache
            WHERE  session_id = %s
            ORDER  BY version DESC
            LIMIT  1
            """,
            (session_id,),
        )
        row = await cur.fetchone()

    if row is None:
        return None
    return {
        "version": row[0],
        "project_id": row[1],
        "db_namespace": row[2],
        "payload": row[3],
        "token_estimate": row[4],
    }


async def write_cache_version(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    *,
    session_id: str,
    project_id: str,
    db_namespace: str | None,
    payload: dict[str, Any],
    token_estimate: int,
) -> int:
    """Insert the next context-cache version for a session. Returns the version.

    The next version number is computed atomically from the current MAX so
    concurrent refreshes within one session do not collide on the UNIQUE
    (session_id, version) constraint.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO session_context_cache (
                session_id, project_id, db_namespace,
                version, payload, token_estimate
            )
            SELECT %s, %s, %s,
                   COALESCE(MAX(version), 0) + 1, %s, %s
            FROM   session_context_cache
            WHERE  session_id = %s
            RETURNING version
            """,
            (
                session_id, project_id, db_namespace,
                Json(payload), token_estimate, session_id,
            ),
        )
        row = await cur.fetchone()
        await conn.commit()

    assert row is not None
    version: int = row[0]
    log.info(
        "context_cache_written",
        session_id=session_id,
        version=version,
        token_estimate=token_estimate,
    )
    return version
