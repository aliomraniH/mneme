from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from agent_service.models import Advisory


class SchemaDriftAdvisor:
    """Compares the two most recent schema snapshots for a namespace.

    Emits a schema_drift advisory when the hashes differ, indicating that
    the upstream database schema changed between refresh_schema calls.
    """

    async def advise(
        self,
        pool: AsyncConnectionPool,  # type: ignore[type-arg]
        db_namespace: str,
    ) -> list[Advisory]:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT schema_hash, captured_at
                FROM   db_schema_snapshot
                WHERE  db_namespace = %s
                ORDER  BY captured_at DESC
                LIMIT  2
                """,
                (db_namespace,),
            )
            rows = await cur.fetchall()

        if len(rows) < 2:
            return []

        newer_hash, newer_at = rows[0]
        older_hash, older_at = rows[1]

        if newer_hash == older_hash:
            return []

        return [
            Advisory(
                kind="schema_drift",
                db_namespace=db_namespace,
                message=(
                    f"Schema changed between {older_at.isoformat()} "
                    f"and {newer_at.isoformat()}. "
                    "Cached queries or column assumptions may be stale. "
                    "Run refresh_schema to get an updated summary."
                ),
                confidence=1.0,
                metadata={
                    "old_hash": older_hash,
                    "new_hash": newer_hash,
                    "old_captured_at": older_at.isoformat(),
                    "new_captured_at": newer_at.isoformat(),
                },
            )
        ]
