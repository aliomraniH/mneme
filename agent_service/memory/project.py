"""project_memory CRUD — the persistent, self-improving brain per (project, db).

Two ranking modes (see embeddings.py):
  * semantic — when a query vector is supplied: hybrid score of
    0.5*cosine_similarity + 0.3*frequency + 0.2*recency.
  * rule-based — when no vector is available: 0.6*frequency + 0.4*recency.

Writes deduplicate: a new memory whose embedding is within
``memory_dedup_threshold`` cosine similarity of an existing one is merged
(content refreshed, call_count bumped) instead of inserting a duplicate row.
When embeddings are unavailable, dedup falls back to exact content match.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from agent_service.memory.episodes import _sanitize

log = structlog.get_logger(__name__)


def _vec_literal(embedding: list[float] | None) -> str | None:
    """Render an embedding as a pgvector literal string, or None."""
    if embedding is None:
        return None
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


async def write_memory(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    *,
    project_id: str,
    db_namespace: str,
    content: str,
    embedding: list[float] | None,
    key_findings: list[str] | None = None,
    entry_type: str = "thread_summary",
    scope: str = "project",
    source: str = "agent_inferred",
    confidence: float = 0.7,
    dedup_threshold: float = 0.92,
) -> dict[str, Any]:
    """Insert (or merge) a project_memory row. Returns {memory_id, action}.

    action is "created" or "merged_with_existing".
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {confidence}")

    safe_content = _sanitize(content)
    safe_findings = [_sanitize(f) for f in (key_findings or [])]
    vec = _vec_literal(embedding)

    async with pool.connection() as conn:
        # ── Dedup check ──────────────────────────────────────────────────────
        existing_id: UUID | None = None
        if vec is not None:
            cur = await conn.execute(
                """
                SELECT id, 1 - (embedding <=> %s::vector) AS sim
                FROM   project_memory
                WHERE  project_id = %s AND db_namespace = %s
                  AND  scope = %s AND embedding IS NOT NULL
                ORDER  BY embedding <=> %s::vector
                LIMIT  1
                """,
                (vec, project_id, db_namespace, scope, vec),
            )
            row = await cur.fetchone()
            if row is not None and row[1] is not None and row[1] >= dedup_threshold:
                existing_id = row[0]
        else:
            cur = await conn.execute(
                """
                SELECT id FROM project_memory
                WHERE  project_id = %s AND db_namespace = %s
                  AND  scope = %s AND content = %s
                LIMIT  1
                """,
                (project_id, db_namespace, scope, safe_content),
            )
            row = await cur.fetchone()
            if row is not None:
                existing_id = row[0]

        # ── Merge ────────────────────────────────────────────────────────────
        if existing_id is not None:
            await conn.execute(
                """
                UPDATE project_memory
                   SET content      = %s,
                       key_findings = %s,
                       call_count   = call_count + 1,
                       confidence   = LEAST(1.0, confidence + 0.05),
                       last_used_at = now(),
                       updated_at   = now()
                 WHERE id = %s
                """,
                (safe_content, Json(safe_findings), existing_id),
            )
            await conn.commit()
            log.info("project_memory_merged", memory_id=str(existing_id))
            return {"memory_id": str(existing_id), "action": "merged_with_existing"}

        # ── Create ───────────────────────────────────────────────────────────
        new_id = uuid4()
        await conn.execute(
            """
            INSERT INTO project_memory (
                id, project_id, db_namespace, scope, entry_type,
                content, key_findings, confidence, source, call_count,
                embedding
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, 0,
                %s::vector
            )
            """,
            (
                new_id, project_id, db_namespace, scope, entry_type,
                safe_content, Json(safe_findings), confidence, source,
                vec,
            ),
        )
        await conn.commit()
    log.info("project_memory_created", memory_id=str(new_id), entry_type=entry_type)
    return {"memory_id": str(new_id), "action": "created"}


async def search_memory(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    *,
    project_id: str,
    db_namespace: str,
    query_vector: list[float] | None,
    include_general: bool = False,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Return ranked project_memory entries. Semantic when query_vector given."""
    vec = _vec_literal(query_vector)

    async with pool.connection() as conn:
        if vec is not None:
            cur = await conn.execute(
                """
                SELECT id, scope, entry_type, content, key_findings,
                       confidence, call_count, last_used_at,
                       1 - (embedding <=> %s::vector) AS sim
                FROM   project_memory
                WHERE  db_namespace = %s
                  AND  embedding IS NOT NULL
                  AND  (project_id = %s
                        OR (%s AND scope = 'general'))
                ORDER  BY
                    0.5 * (1 - (embedding <=> %s::vector))
                  + 0.3 * LEAST(call_count, 20) / 20.0
                  + 0.2 * exp(
                        -extract(epoch FROM (now() - last_used_at)) / 604800.0
                    )
                    DESC
                LIMIT  %s
                """,
                (vec, db_namespace, project_id, include_general, vec, limit),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, scope, entry_type, content, key_findings,
                       confidence, call_count, last_used_at,
                       NULL::real AS sim
                FROM   project_memory
                WHERE  db_namespace = %s
                  AND  (project_id = %s
                        OR (%s AND scope = 'general'))
                ORDER  BY
                    0.6 * LEAST(call_count, 20) / 20.0
                  + 0.4 * exp(
                        -extract(epoch FROM (now() - last_used_at)) / 604800.0
                    )
                    DESC
                LIMIT  %s
                """,
                (db_namespace, project_id, include_general, limit),
            )
        rows = await cur.fetchall()

    return [
        {
            "id": str(r[0]),
            "scope": r[1],
            "entry_type": r[2],
            "content": r[3],
            "key_findings": r[4] or [],
            "confidence": r[5],
            "call_count": r[6],
            "last_used_at": r[7].isoformat() if r[7] else None,
            "similarity": round(float(r[8]), 4) if r[8] is not None else None,
            "context_key": f"{r[2]}:{r[0]}",
        }
        for r in rows
    ]


async def increment_call_counts(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    memory_ids: list[str],
) -> None:
    """Bump call_count and freshen last_used_at for entries that were injected."""
    if not memory_ids:
        return
    async with pool.connection() as conn:
        await conn.execute(
            """
            UPDATE project_memory
               SET call_count = call_count + 1, last_used_at = now()
             WHERE id = ANY(%s::uuid[])
            """,
            (memory_ids,),
        )
        await conn.commit()


async def list_memory(
    pool: AsyncConnectionPool,  # type: ignore[type-arg]
    project_id: str,
    db_namespace: str | None = None,
    include_general: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return all memory entries for a project (newest-used first), for display."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, db_namespace, scope, entry_type, content,
                   key_findings, confidence, call_count, last_used_at, source
            FROM   project_memory
            WHERE  (project_id = %s OR (%s AND scope = 'general'))
              AND  (%s::text IS NULL OR db_namespace = %s)
            ORDER  BY last_used_at DESC
            LIMIT  %s
            """,
            (project_id, include_general, db_namespace, db_namespace, limit),
        )
        rows = await cur.fetchall()

    return [
        {
            "id": str(r[0]),
            "db_namespace": r[1],
            "scope": r[2],
            "entry_type": r[3],
            "content": r[4],
            "key_findings": r[5] or [],
            "confidence": r[6],
            "call_count": r[7],
            "last_used_at": r[8].isoformat() if r[8] else None,
            "source": r[9],
        }
        for r in rows
    ]
