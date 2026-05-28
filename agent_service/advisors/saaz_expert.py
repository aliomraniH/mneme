"""Domain-expert advisor for the saaz_demo namespace (Persian/jazz music catalog).

Checks performed
----------------
1. schema_completeness   — expected tables present in latest snapshot
2. embedding_coverage    — recent saaz_search_artists calls vs saaz_list_artists
                           ratio; warns if artists appear un-embedded
3. provenance_gap        — warns when refresh_schema finds fewer tables than
                           the known minimum (6), suggesting a schema regression
4. stale_schema          — warns when no schema snapshot exists at all, or the
                           most recent one is > 24 hours old
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg_pool import AsyncConnectionPool

from agent_service.advisors.expert_base import DomainExpertAdvisor, register_expert
from agent_service.models import Advisory

_NAMESPACE = "saaz_demo"
_EXPECTED_TABLES = {"artist", "artist_image", "artist_link",
                    "data_provenance", "enrichment_run", "song"}
_MIN_TABLE_COUNT = len(_EXPECTED_TABLES)
_SCHEMA_STALE_HOURS = 24


class SaazMusicExpert(DomainExpertAdvisor):
    """Per-namespace expert for the Saaz Persian/jazz music catalog."""

    namespace = _NAMESPACE

    async def advise(
        self,
        pool: AsyncConnectionPool,  # type: ignore[type-arg]
        db_namespace: str,
    ) -> list[Advisory]:
        if db_namespace != _NAMESPACE:
            return []

        advisories: list[Advisory] = []

        async with pool.connection() as conn:
            # ── 1. Schema completeness + recency ────────────────────────────
            cur = await conn.execute(
                """
                SELECT tables, captured_at
                FROM   db_schema_snapshot
                WHERE  db_namespace = %s
                ORDER  BY captured_at DESC
                LIMIT  1
                """,
                (db_namespace,),
            )
            snap_row = await cur.fetchone()

        if snap_row is None:
            advisories.append(Advisory(
                kind="domain_expert",
                db_namespace=db_namespace,
                message=(
                    "No schema snapshot found for saaz_demo. "
                    "Call refresh_schema to capture the current table layout "
                    "so schema-drift and completeness checks can run."
                ),
                confidence=0.9,
                metadata={"check": "stale_schema", "namespace": db_namespace},
            ))
            return advisories

        tables_json: list[dict] = snap_row[0] or []
        captured_at: datetime = snap_row[1]
        table_names = {t.get("name", "") for t in tables_json}

        # ── 1a. Stale snapshot ────────────────────────────────────────────
        age = datetime.now(UTC) - captured_at.replace(tzinfo=UTC)
        if age > timedelta(hours=_SCHEMA_STALE_HOURS):
            advisories.append(Advisory(
                kind="domain_expert",
                db_namespace=db_namespace,
                message=(
                    f"Schema snapshot for saaz_demo is {age.seconds // 3600}h "
                    f"{(age.seconds % 3600) // 60}m old "
                    f"(captured {captured_at.isoformat()}). "
                    "Call refresh_schema to check for upstream changes."
                ),
                confidence=0.75,
                metadata={
                    "check": "stale_schema",
                    "namespace": db_namespace,
                    "captured_at": captured_at.isoformat(),
                    "age_hours": round(age.total_seconds() / 3600, 1),
                },
            ))

        # ── 1b. Missing expected tables ───────────────────────────────────
        missing = _EXPECTED_TABLES - table_names
        if missing:
            advisories.append(Advisory(
                kind="domain_expert",
                db_namespace=db_namespace,
                message=(
                    f"saaz_demo schema is missing expected table(s): "
                    f"{sorted(missing)}. "
                    "Queries joining those tables will fail."
                ),
                confidence=0.95,
                metadata={
                    "check": "schema_completeness",
                    "namespace": db_namespace,
                    "missing_tables": sorted(missing),
                    "present_tables": sorted(table_names),
                },
            ))

        # ── 1c. Table count regression ────────────────────────────────────
        if 0 < len(table_names) < _MIN_TABLE_COUNT:
            advisories.append(Advisory(
                kind="domain_expert",
                db_namespace=db_namespace,
                message=(
                    f"saaz_demo snapshot shows only {len(table_names)} table(s) "
                    f"(expected at least {_MIN_TABLE_COUNT}). "
                    "The upstream may be cold-starting; call refresh_schema again."
                ),
                confidence=0.8,
                metadata={
                    "check": "provenance_gap",
                    "namespace": db_namespace,
                    "found_count": len(table_names),
                    "expected_min": _MIN_TABLE_COUNT,
                },
            ))

        # ── 2. Embedding coverage (audit-based) ───────────────────────────
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE tool_name = 'saaz_search_artists') AS search_calls,
                    COUNT(*) FILTER (WHERE tool_name = 'saaz_list_artists')   AS list_calls,
                    COUNT(*) FILTER (WHERE tool_name = 'saaz_query'
                                      AND  error IS NOT NULL)                  AS query_errors
                FROM   query_episode
                WHERE  db_namespace = %s
                  AND  ts > now() - interval '6 hours'
                """,
                (db_namespace,),
            )
            row = await cur.fetchone()

        if row:
            search_calls, list_calls, query_errors = row
            # If there are list calls but zero search calls in recent history,
            # it suggests vector embeddings may not be populated.
            if list_calls >= 3 and search_calls == 0:
                advisories.append(Advisory(
                    kind="domain_expert",
                    db_namespace=db_namespace,
                    message=(
                        f"saaz_search_artists has not been called in the last 6 hours "
                        f"({list_calls} list_artists calls recorded). "
                        "If semantic search is expected, artist embeddings may be "
                        "missing or the search tool is not being used."
                    ),
                    confidence=0.6,
                    metadata={
                        "check": "embedding_coverage",
                        "namespace": db_namespace,
                        "search_calls_6h": search_calls,
                        "list_calls_6h": list_calls,
                    },
                ))

            # Elevated query errors warn about likely schema/SQL mismatch
            if query_errors >= 3:
                advisories.append(Advisory(
                    kind="domain_expert",
                    db_namespace=db_namespace,
                    message=(
                        f"saaz_query has returned {query_errors} error(s) in the last "
                        "6 hours. Schema drift or incorrect column references may be "
                        "causing failures — run refresh_schema and review recent errors "
                        "with get_query_history(only_errors=True)."
                    ),
                    confidence=min(0.95, 0.6 + query_errors * 0.05),
                    metadata={
                        "check": "query_error_spike",
                        "namespace": db_namespace,
                        "query_errors_6h": query_errors,
                    },
                ))

        return advisories


# Register the expert at import time so get_experts("saaz_demo") finds it.
register_expert(SaazMusicExpert())
