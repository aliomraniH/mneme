from __future__ import annotations

import asyncio
import uuid as _uuid
from collections.abc import Callable
from typing import Any

import mcp.types as mt
import structlog
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext, Middleware
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger(__name__)

_IDLE_TIMEOUT_MINUTES = 30

# Keyed by fastmcp_ctx.session_id (a stable per-session UUID).
# Stores client metadata captured during on_initialize so that on_call_tool
# can INSERT the session row with the correct HTTP transport session ID.
# Entries are popped on first tool call to avoid unbounded growth.
_pending_metadata: dict[str, dict[str, Any]] = {}


def _normalize_sid(raw: str) -> str:
    """Convert a 32-char hex session ID (no dashes) to UUID-with-dashes format.

    The MCP SDK's StreamableHTTP transport generates session IDs as
    ``uuid4().hex`` (32 lowercase hex chars, no dashes).  The DB column is TEXT
    and we store the canonical UUID string ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``
    so that lookups from the test (which does ``str(uuid.UUID(header_value))``)
    match what is stored.
    """
    try:
        return str(_uuid.UUID(raw))
    except ValueError:
        return raw


class SessionMiddleware(Middleware):
    """Tracks MCP sessions in the mcp_session table.

    On initialize: captures client metadata in-memory (the HTTP transport session
    ID is not yet available in the request headers at this point, because this is
    the very first request for a new session).

    On first tool call: inserts the session row using the ``mcp-session-id``
    HTTP request header as the primary key and populates it with the metadata
    collected during initialize.

    On every subsequent tool call: updates last_seen_at and increments counters.

    On reconnect (initialize with an existing ``mcp-session-id`` in headers):
    upserts the row directly from on_initialize.
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
            fastmcp_sid = fastmcp_ctx.session_id
            client_info = context.message.params.clientInfo
            client_name = client_info.name if client_info else None
            client_version = client_info.version if client_info else None

            client_ip: str | None = None
            user_agent: str | None = None
            existing_http_sid: str | None = None
            try:
                from fastmcp.server.dependencies import get_http_request

                req = get_http_request()
                client_ip = req.headers.get("x-forwarded-for") or (
                    req.client.host if req.client else None
                )
                user_agent = req.headers.get("user-agent")
                raw = req.headers.get("mcp-session-id")
                existing_http_sid = _normalize_sid(raw) if raw else None
            except Exception:
                pass

            if existing_http_sid:
                # Reconnecting client: session row already exists; upsert it.
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
                        (existing_http_sid, client_name, client_version, client_ip, user_agent),
                    )
                    await conn.commit()
                log.info(
                    "session_started",
                    session_id=existing_http_sid,
                    client_name=client_name,
                    client_ip=client_ip,
                )
            else:
                # First connection: the HTTP transport will assign a session ID and
                # return it in the response header.  We cannot read that header here
                # (it is added after the handler returns), so we cache the metadata
                # and let on_call_tool insert the row once the header is visible in
                # the next request.
                _pending_metadata[fastmcp_sid] = {
                    "client_name": client_name,
                    "client_version": client_version,
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                }
        except Exception as exc:
            log.warning("session_init_failed", error=str(exc))

        return result

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, Any],
    ) -> Any:
        fastmcp_ctx = context.fastmcp_context

        had_error = False
        result: Any = None
        try:
            result = await call_next(context)
        except Exception:
            had_error = True
            raise
        finally:
            if fastmcp_ctx is not None:
                try:
                    fastmcp_sid = fastmcp_ctx.session_id

                    # Prefer the HTTP transport session ID (always present on tool calls).
                    http_sid: str | None = None
                    try:
                        from fastmcp.server.dependencies import get_http_request

                        raw = get_http_request().headers.get("mcp-session-id")
                        http_sid = _normalize_sid(raw) if raw else None
                    except Exception:
                        pass

                    session_id = http_sid or fastmcp_sid

                    # Pop pending metadata (set during on_initialize for new sessions).
                    meta = _pending_metadata.pop(fastmcp_sid, None)

                    pool = self._pool_factory()
                    async with pool.connection() as conn:
                        if meta is not None:
                            # First tool call after a fresh initialize: insert session row.
                            await conn.execute(
                                """
                                INSERT INTO mcp_session (
                                    session_id, client_name, client_version,
                                    client_ip, user_agent
                                ) VALUES (%s, %s, %s, %s::inet, %s)
                                ON CONFLICT (session_id) DO NOTHING
                                """,
                                (
                                    session_id,
                                    meta["client_name"],
                                    meta["client_version"],
                                    meta["client_ip"],
                                    meta["user_agent"],
                                ),
                            )
                            log.info(
                                "session_started",
                                session_id=session_id,
                                client_name=meta["client_name"],
                                client_ip=meta["client_ip"],
                            )

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
