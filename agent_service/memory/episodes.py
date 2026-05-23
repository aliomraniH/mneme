from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from agent_service.models import Episode

log = structlog.get_logger(__name__)

# Strip <|...|>-style tokens and obvious injection phrases before writing to DB.
_TOKEN_RE = re.compile(r"<\|[^|]*\|>")
_INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "forget previous",
    "new task:",
    "system prompt:",
)


def _sanitize(text: str) -> str:
    text = _TOKEN_RE.sub("", text)
    lower = text.lower()
    for phrase in _INJECTION_PHRASES:
        if phrase in lower:
            start = lower.find(phrase)
            text = text[:start] + "[REDACTED]" + text[start + len(phrase) :]
            lower = text.lower()
    return text


def _sanitize_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    if "truncated_payload" in summary:
        sanitized = _sanitize(str(summary["truncated_payload"]))
        return {"truncated_payload": sanitized}
    # Sanitize string values one level deep; nested payloads are not recursed
    # (they come from mneme's own digest, not raw user data).
    return {k: _sanitize(v) if isinstance(v, str) else v for k, v in summary.items()}


async def write_episode(
    pool: AsyncConnectionPool,
    episode: Episode,
) -> UUID:
    """Insert one query_episode row. Never raises — callers rely on fire-and-forget."""
    safe_summary = _sanitize_summary(episode.result_summary)

    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO query_episode (
                    id, db_namespace, thread_id, tool_name,
                    user_query, tool_params, result_summary,
                    row_count, duration_ms, error, source,
                    audit_id, ts,
                    session_id, client_name, client_version,
                    client_ip, user_agent, truncated
                ) VALUES (
                    %(id)s, %(db_namespace)s, %(thread_id)s, %(tool_name)s,
                    %(user_query)s, %(tool_params)s, %(result_summary)s,
                    %(row_count)s, %(duration_ms)s, %(error)s, %(source)s,
                    %(audit_id)s, %(ts)s,
                    %(session_id)s, %(client_name)s, %(client_version)s,
                    %(client_ip)s, %(user_agent)s, %(truncated)s
                )
                """,
                {
                    "id": episode.id,
                    "db_namespace": episode.db_namespace,
                    "thread_id": episode.thread_id,
                    "tool_name": episode.tool_name,
                    "user_query": episode.user_query,
                    "tool_params": Json(episode.tool_params),
                    "result_summary": (
                        Json(safe_summary) if safe_summary is not None else None
                    ),
                    "row_count": episode.row_count,
                    "duration_ms": episode.duration_ms,
                    "error": episode.error,
                    "source": episode.source,
                    "audit_id": episode.audit_id,
                    "ts": episode.ts,
                    "session_id": episode.session_id,
                    "client_name": episode.client_name,
                    "client_version": episode.client_version,
                    "client_ip": episode.client_ip,
                    "user_agent": episode.user_agent,
                    "truncated": episode.truncated,
                },
            )
            await conn.commit()
    except Exception as exc:
        log.warning("write_episode_failed", error=str(exc), audit_id=str(episode.audit_id))
        return episode.id

    return episode.id


async def get_recent_episodes(
    pool: AsyncConnectionPool,
    db_namespace: str,
    limit: int = 20,
    tool_name: str | None = None,
) -> list[Episode]:
    """Return recent episodes for a namespace, newest first."""
    rows: list[Any]
    async with pool.connection() as conn:
        if tool_name is not None:
            cur = await conn.execute(
                """
                SELECT id, db_namespace, thread_id, tool_name,
                       user_query, tool_params, result_summary,
                       row_count, duration_ms, error, source,
                       audit_id, ts,
                       session_id, client_name, client_version,
                       client_ip::text, user_agent, truncated
                  FROM query_episode
                 WHERE db_namespace = %s AND tool_name = %s
                 ORDER BY ts DESC
                 LIMIT %s
                """,
                (db_namespace, tool_name, limit),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, db_namespace, thread_id, tool_name,
                       user_query, tool_params, result_summary,
                       row_count, duration_ms, error, source,
                       audit_id, ts,
                       session_id, client_name, client_version,
                       client_ip::text, user_agent, truncated
                  FROM query_episode
                 WHERE db_namespace = %s
                 ORDER BY ts DESC
                 LIMIT %s
                """,
                (db_namespace, limit),
            )
        rows = await cur.fetchall()

    return [
        Episode(
            id=row[0],
            db_namespace=row[1],
            thread_id=row[2],
            tool_name=row[3],
            user_query=row[4],
            tool_params=row[5] or {},
            result_summary=row[6],
            row_count=row[7],
            duration_ms=row[8],
            error=row[9],
            source=row[10],
            audit_id=row[11],
            ts=row[12],
            session_id=row[13],
            client_name=row[14],
            client_version=row[15],
            client_ip=row[16],
            user_agent=row[17],
            truncated=row[18],
        )
        for row in rows
    ]
