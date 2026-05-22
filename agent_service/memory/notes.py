from __future__ import annotations

from uuid import UUID, uuid4

from psycopg_pool import AsyncConnectionPool

from agent_service.errors import MemoryWriteError
from agent_service.memory.episodes import _sanitize


async def write_expertise_note(
    pool: AsyncConnectionPool,
    db_namespace: str,
    note: str,
    confidence: float,
    trigger_pattern: str | None = None,
) -> UUID:
    """Insert one expertise_note row.

    The embedding column is left NULL for Phase 1; Phase 2 will fill it
    when the Anthropic embeddings call is wired up.
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {confidence}")

    note_id = uuid4()
    safe_note = _sanitize(note)

    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO expertise_note (
                    id, db_namespace, note, trigger_pattern,
                    confidence, embedding
                ) VALUES (
                    %s, %s, %s, %s, %s, NULL
                )
                """,
                (note_id, db_namespace, safe_note, trigger_pattern, confidence),
            )
            await conn.commit()
    except Exception as exc:
        raise MemoryWriteError(f"write_expertise_note failed: {exc}") from exc

    return note_id
