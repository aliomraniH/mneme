from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from agent_service.models import Advisory


class CacheStaleAdvisor:
    """Checks cache_event for entries that have exceeded their TTL without invalidation.

    Emits a cache_stale advisory for each stale entry (up to 5 per call).
    """

    async def advise(
        self,
        pool: AsyncConnectionPool,  # type: ignore[type-arg]
        db_namespace: str,
    ) -> list[Advisory]:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT cache_key, written_at, ttl_seconds
                FROM   cache_event
                WHERE  db_namespace = %s
                  AND  invalidated_at IS NULL
                  AND  ttl_seconds IS NOT NULL
                  AND  written_at + (ttl_seconds || ' seconds')::interval < now()
                ORDER  BY written_at DESC
                LIMIT  5
                """,
                (db_namespace,),
            )
            rows = await cur.fetchall()

        advisories: list[Advisory] = []
        for cache_key, written_at, ttl_seconds in rows:
            advisories.append(
                Advisory(
                    kind="cache_stale",
                    db_namespace=db_namespace,
                    message=(
                        f"Cached result for key {cache_key!r} is stale "
                        f"(written {written_at.isoformat()}, TTL {ttl_seconds}s). "
                        "Consider refreshing before using this result."
                    ),
                    confidence=0.95,
                    metadata={
                        "cache_key": cache_key,
                        "written_at": written_at.isoformat(),
                        "ttl_seconds": ttl_seconds,
                    },
                )
            )
        return advisories
