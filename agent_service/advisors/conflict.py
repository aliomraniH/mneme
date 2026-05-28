from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from agent_service.models import Advisory


class ConflictAdvisor:
    """Detects row-count variance across recent episodes for the same tool.

    Emits a potential_conflict advisory when the same tool returns meaningfully
    different row counts within the last hour, suggesting data is changing
    rapidly or a query parameter inconsistency exists.
    """

    def __init__(self, threshold: float = 0.05) -> None:
        # Minimum fractional divergence to trigger an advisory.
        self._threshold = threshold

    async def advise(
        self,
        pool: AsyncConnectionPool,  # type: ignore[type-arg]
        db_namespace: str,
    ) -> list[Advisory]:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT tool_name,
                       min(row_count)  AS min_rows,
                       max(row_count)  AS max_rows,
                       count(*)        AS call_count
                FROM   query_episode
                WHERE  db_namespace = %s
                  AND  source = 'ok'
                  AND  row_count IS NOT NULL
                  AND  row_count > 0
                  AND  ts > now() - interval '1 hour'
                GROUP  BY tool_name
                HAVING min(row_count) != max(row_count)
                ORDER  BY
                    (max(row_count) - min(row_count))::float
                    / NULLIF(min(row_count), 0) DESC
                LIMIT  3
                """,
                (db_namespace,),
            )
            rows = await cur.fetchall()

        advisories: list[Advisory] = []
        for tool_name, min_rows, max_rows, call_count in rows:
            divergence = (max_rows - min_rows) / max(min_rows, 1)
            if divergence < self._threshold:
                continue
            advisories.append(
                Advisory(
                    kind="potential_conflict",
                    db_namespace=db_namespace,
                    message=(
                        f"Tool {tool_name!r} returned between {min_rows} and "
                        f"{max_rows} rows across {call_count} calls in the last "
                        f"hour ({divergence:.0%} variance). Data may be changing "
                        "rapidly or queries are using different filters."
                    ),
                    confidence=min(0.9, divergence),
                    metadata={
                        "tool_name": tool_name,
                        "min_rows": min_rows,
                        "max_rows": max_rows,
                        "call_count": call_count,
                        "divergence_pct": round(divergence * 100, 1),
                    },
                )
            )
        return advisories
