from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import mcp.types as mt
import structlog
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext, Middleware
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger(__name__)

_IDLE_TIMEOUT_MINUTES = 30


class SessionMiddleware(Middleware):
    """Tracks MCP sessions in the mcp_session table.

    On initialize: inserts a row with client metadata.
    On every tool call: updates last_seen_at and increments counters.
    """

    def __init__(self, pool_factory: Callable[[], AsyncConnectionPool]) -> None:  # type: ignore[type-arg]
        self._pool_factory = pool_factory

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, mt.InitializeResult | None],
    ) -> mt.InitializeResult | None:
        result = await call_next(context)

        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None:
            return result

        try:
            session_id = fastmcp_ctx.session_id
            client_info = context.message.params.clientInfo
            client_name = client_info.name if client_info else None
            client_version = client_info.version if client_info else None

            client_ip: str | None = None
            user_agent: str | None = None
            try:
                from fastmcp.server.dependencies import get_http_request

                req = get_http_request()
                client_ip = req.headers.get("x-forwarded-for") or (
                    req.client.host if req.client else None
                )
                user_agent = req.headers.get("user-agent")
            except Exception:
                pass

            pool = self._pool_factory()
            async with pool.connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO mcp_session (
                        session_id, client_name, client_version,
                        client_ip, user_agent
                    ) VALUES (%s, %s, %s, %s::inet, %s)
                    ON CONFLICT (session_id) DO UPDATE
                        SET last_seen_at = now()
                    """,
                    (session_id, client_name, client_version, client_ip, user_agent),
                )
                await conn.commit()

            log.info(
                "session_started",
                session_id=session_id,
                client_name=client_name,
                client_ip=client_ip,
            )
        except Exception as exc:
            log.warning("session_init_failed", error=str(exc))

        return result

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, Any],
    ) -> Any:
        result = await call_next(context)

        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None:
            return result

        try:
            session_id = fastmcp_ctx.session_id
            had_error = isinstance(result, Exception)
            pool = self._pool_factory()
            async with pool.connection() as conn:
                await conn.execute(
                    """
                    UPDATE mcp_session
                       SET last_seen_at = now(),
                           total_calls  = total_calls + 1,
                           total_errors = total_errors + %s
                     WHERE session_id = %s
                    """,
                    (1 if had_error else 0, session_id),
                )
                await conn.commit()
        except Exception as exc:
            log.warning("session_update_failed", error=str(exc))

        return result


async def idle_session_reaper(
    pool_factory: Callable[[], AsyncConnectionPool],  # type: ignore[type-arg]
    idle_seconds: int = 1800,
    check_interval_seconds: int = 60,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Background task: mark idle sessions ended every check_interval_seconds."""
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            break
        try:
            await asyncio.sleep(check_interval_seconds)
        except asyncio.CancelledError:
            break

        try:
            pool = pool_factory()
            async with pool.connection() as conn:
                cur = await conn.execute(
                    """
                    UPDATE mcp_session
                       SET ended_at   = now(),
                           end_reason = 'idle_timeout'
                     WHERE ended_at IS NULL
                       AND last_seen_at < now() - make_interval(secs => %s)
                    RETURNING session_id
                    """,
                    (idle_seconds,),
                )
                rows = await cur.fetchall()
                await conn.commit()
            if rows:
                log.info("sessions_reaped_idle", count=len(rows))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("idle_reaper_error", error=str(exc))


async def mark_sessions_shutdown(pool: AsyncConnectionPool) -> None:  # type: ignore[type-arg]
    """Mark all open sessions as ended with reason='shutdown'. Called in lifespan teardown."""
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """
                UPDATE mcp_session
                   SET ended_at   = now(),
                       end_reason = 'shutdown'
                 WHERE ended_at IS NULL
                """
            )
            await conn.commit()
    except Exception as exc:
        log.warning("mark_sessions_shutdown_failed", error=str(exc))
