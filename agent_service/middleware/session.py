from __future__ import annotations

import asyncio
import time
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

# ---------------------------------------------------------------------------
# _pending_metadata  (first-call bootstrap, Task 3 / Task 5)
# ---------------------------------------------------------------------------
# Keyed by fastmcp_ctx.session_id (a stable per-session UUID assigned by
# FastMCP before the HTTP transport session ID is known).
#
# Problem: the MCP initialize request creates the HTTP transport session and
# its ID is returned in the *response* header — we cannot read it inside the
# on_initialize handler because the header is added after the handler returns.
# Solution: cache the client metadata here; on_call_tool pops the entry on
# the very first tool call, when the "mcp-session-id" request header is
# already present.
#
# Entries are popped on first tool call to avoid unbounded growth.
_pending_metadata: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# _session_client_info  (persistent per-session cache, Tasks 5 & 6)
# ---------------------------------------------------------------------------
# Keyed by the canonical session_id (UUID string with dashes, same form used
# in the mcp_session table primary key).
#
# Why this exists (Task 5):
#   AuditMiddleware runs on EVERY tool call and needs client_name/version to
#   populate the query_episode row.  Before this cache, those fields were only
#   available during on_initialize (the one request that carries clientInfo in
#   the MCP protocol).  Subsequent calls had no access to that data.
#
# How it is populated:
#   After a successful INSERT into mcp_session (Phase 1 of on_call_tool, or
#   the upsert in the reconnect path of on_initialize), the entry is written
#   here.  AuditMiddleware reads it via get_session_client_info().
#
# Memory bound (Task 6):
#   idle_session_reaper evicts entries for sessions it marks ended in the DB.
#   It also runs a TTL sweep: any entry older than idle_seconds is evicted
#   even if the DB reap found nothing (guards against sessions that never
#   made a tool call).
#   mark_sessions_shutdown() clears the dict entirely on process shutdown.
_session_client_info: dict[str, dict[str, Any]] = {}


def get_session_client_info(session_id: str) -> dict[str, Any] | None:
    """Return cached clientInfo dict for session_id, or None if not found.

    Used by AuditMiddleware to populate client_name / client_version on every
    tool call without re-reading from the DB.
    """
    return _session_client_info.get(session_id)


def _resolve_client_ip(
    *,
    xff: str | None,
    peer_host: str | None,
    trusted_proxy_hops: int = 0,
) -> str | None:
    """Resolve the effective client IP from request metadata.  (Task 10)

    Security motivation:
        The X-Forwarded-For header is trivially forged by any caller.  Reading
        XFF[0] directly (the old behaviour) allowed a client to claim any IP
        it liked.  The safe default is to trust only the TCP peer address
        (req.client.host), which is set by the kernel and cannot be spoofed.

    trusted_proxy_hops=0 (default):
        Return peer_host — the actual TCP peer, always trustworthy.

    trusted_proxy_hops=N (N > 0):
        Each trusted reverse proxy appends the previous hop's IP to XFF.  The
        rightmost N entries were added by our own trusted proxies; the entry
        at position -(N+1) from the right is the outermost untrusted IP, which
        is the real client.  Falls back to peer_host when XFF is absent or
        does not contain enough entries to satisfy the hop count.

    Example (trusted_proxy_hops=2, XFF="1.2.3.4, 10.0.0.1, 10.0.0.2"):
        parts = ["1.2.3.4", "10.0.0.1", "10.0.0.2"]
        We need len(parts) > 2, it is (len=3), so return parts[-(2+1)] = "1.2.3.4"
    """
    if trusted_proxy_hops == 0 or xff is None:
        return peer_host
    parts = [p.strip() for p in xff.split(",")]
    # We need at least N+1 entries to safely trust entry [-(N+1)].
    # If the chain is shorter, fall back to the peer (conservative).
    if len(parts) <= trusted_proxy_hops:
        return peer_host
    return parts[-(trusted_proxy_hops + 1)]


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

    Lifecycle:

    on_initialize (new session):
        The HTTP transport session ID is not yet visible here (it is added to
        the response header after this handler returns).  We save the client
        metadata to _pending_metadata keyed by fastmcp_ctx.session_id and
        return.  No DB write yet.

    on_call_tool — Phase 1 (first tool call only):
        Pop the pending metadata entry and INSERT the mcp_session row using
        the "mcp-session-id" HTTP header as the primary key.
        After commit, set session_row_exists = True and write to
        _session_client_info so AuditMiddleware can read it.

        Task 3 / Task 7 fix — FK violation guard:
            session_row_exists starts as False and is only set True after
            conn.commit() succeeds.  If the INSERT raises (e.g. transient DB
            error), we set session_id = None and session_row_exists = False so
            the Phase 3 UPDATE is skipped and AuditMiddleware writes NULL
            session_id — preventing a FK violation in query_episode.

    on_call_tool — Phase 2:
        Execute the actual tool call via call_next().

    on_call_tool — Phase 3 (every tool call, in finally):
        UPDATE mcp_session counters (last_seen_at, total_calls, total_errors).
        Guarded on `session_id is not None and session_row_exists` (Task 7):
        never attempts the UPDATE against a row that was never committed.

    on_initialize (reconnect):
        The client sends the existing session ID in the "mcp-session-id"
        request header.  We upsert the row directly and populate
        _session_client_info without overwriting an existing live entry.
    """

    def __init__(self, pool_factory: Callable[[], AsyncConnectionPool]) -> None:
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
            project_id: str | None = None
            existing_http_sid: str | None = None
            try:
                from fastmcp.server.dependencies import get_http_request

                req = get_http_request()
                _xff = req.headers.get("x-forwarded-for")
                _peer = req.client.host if req.client else None
                # on_initialize always uses hops=0 (peer) because TRUSTED_PROXY_HOPS
                # is not yet loaded here; the full config-driven resolution happens
                # in on_call_tool via AuditMiddleware.
                client_ip = _resolve_client_ip(
                    xff=_xff, peer_host=_peer, trusted_proxy_hops=0
                )
                user_agent = req.headers.get("user-agent")
                project_id = req.headers.get("x-mneme-project")
                raw = req.headers.get("mcp-session-id")
                existing_http_sid = _normalize_sid(raw) if raw else None
            except Exception:
                pass

            if existing_http_sid:
                # ── Reconnect path ──────────────────────────────────────────
                # The client already has a session; upsert the row so that
                # last_seen_at stays fresh even on reconnect.
                pool = self._pool_factory()
                async with pool.connection() as conn:
                    await conn.execute(
                        """
                        INSERT INTO mcp_session (
                            session_id, client_name, client_version,
                            client_ip, user_agent, project_id
                        ) VALUES (%s, %s, %s, %s::inet, %s, %s)
                        ON CONFLICT (session_id) DO UPDATE
                            SET last_seen_at = now()
                        """,
                        (
                            existing_http_sid, client_name, client_version,
                            client_ip, user_agent, project_id,
                        ),
                    )
                    await conn.commit()
                # Persist client info for the reconnected session.
                # Do NOT overwrite an existing live entry — it may already hold
                # richer data from the initial connection (Task 5).
                if existing_http_sid not in _session_client_info:
                    _session_client_info[existing_http_sid] = {
                        "client_name": client_name,
                        "client_version": client_version,
                        "inserted_at": time.monotonic(),
                    }
                log.info(
                    "session_started",
                    session_id=existing_http_sid,
                    client_name=client_name,
                    client_ip=client_ip,
                )
            else:
                # ── First-connection path ───────────────────────────────────
                # The HTTP transport will assign the session ID and return it
                # in the response header.  We cannot read that header here
                # (it is added after this handler returns), so cache the
                # metadata and let on_call_tool INSERT the row on the next
                # request when the header is already present.
                _pending_metadata[fastmcp_sid] = {
                    "client_name": client_name,
                    "client_version": client_version,
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                    "project_id": project_id,
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
        session_id: str | None = None

        # ── Phase 1: ensure the session row EXISTS before the tool executes ──
        #
        # Why Phase 1 must complete before call_next (Task 3 / Task 7):
        #   AuditMiddleware is an inner middleware whose finally block runs
        #   BEFORE this finally block (inner middleware unwinds first).
        #   query_episode.session_id is a FK → mcp_session.session_id, so the
        #   mcp_session row must be committed before AuditMiddleware writes the
        #   episode.  If we deferred the INSERT to the finally block here it
        #   would run AFTER the FK-constrained episode INSERT, causing a
        #   violation.
        #
        # FK-safe flag (Task 3):
        #   session_row_exists starts False.  It is only set True after
        #   conn.commit() returns successfully.  If the INSERT raises, we set
        #   session_id = None so AuditMiddleware writes NULL (no FK) and Phase
        #   3 skips the UPDATE (Task 7).
        session_row_exists = False  # True only after a successful DB commit
        if fastmcp_ctx is not None:
            try:
                fastmcp_sid = fastmcp_ctx.session_id

                http_sid: str | None = None
                try:
                    from fastmcp.server.dependencies import get_http_request

                    raw = get_http_request().headers.get("mcp-session-id")
                    http_sid = _normalize_sid(raw) if raw else None
                except Exception:
                    pass

                # Prefer the HTTP transport session ID (stable, matches the DB
                # PK); fall back to the fastmcp internal ID for non-HTTP transports.
                session_id = http_sid or fastmcp_sid

                # Pop pending metadata captured during on_initialize.
                # None means this is not the first tool call for this session.
                meta = _pending_metadata.pop(fastmcp_sid, None)
                if meta is not None:
                    # ── First tool call: INSERT the session row ─────────────
                    pool = self._pool_factory()
                    async with pool.connection() as conn:
                        await conn.execute(
                            """
                            INSERT INTO mcp_session (
                                session_id, client_name, client_version,
                                client_ip, user_agent, project_id
                            ) VALUES (%s, %s, %s, %s::inet, %s, %s)
                            ON CONFLICT (session_id) DO NOTHING
                            """,
                            (
                                session_id,
                                meta["client_name"],
                                meta["client_version"],
                                meta["client_ip"],
                                meta["user_agent"],
                                meta.get("project_id"),
                            ),
                        )
                        await conn.commit()
                    # Only set True here — after commit succeeds (Task 3).
                    session_row_exists = True
                    # Populate the persistent cache so AuditMiddleware can read
                    # client_name/version on this and all future calls (Task 5).
                    _session_client_info[session_id] = {
                        "client_name": meta["client_name"],
                        "client_version": meta["client_version"],
                        "inserted_at": time.monotonic(),
                    }
                    log.info(
                        "session_started",
                        session_id=session_id,
                        client_name=meta["client_name"],
                        client_ip=meta["client_ip"],
                    )
                else:
                    # No pending metadata → row was inserted on the reconnect
                    # path in on_initialize; assume it exists.
                    session_row_exists = True
            except Exception as exc:
                log.warning("session_setup_failed", error=str(exc))
                # Do NOT let a broken session setup propagate a FK violation.
                # Nulling session_id causes AuditMiddleware to write NULL
                # (allowed by the column) and Phase 3 to skip the UPDATE
                # (Task 3 + Task 7).
                session_id = None
                session_row_exists = False

        # ── Phase 2: execute the tool call ──────────────────────────────────
        had_error = False
        result: Any = None
        try:
            result = await call_next(context)
        except Exception:
            had_error = True
            raise
        finally:
            # ── Phase 3: update counters ─────────────────────────────────────
            # Guarded on session_row_exists (Task 7): if Phase 1 never committed
            # a row, this UPDATE would silently match zero rows at best or raise
            # a FK error at worst — skip it entirely.
            if session_id is not None and session_row_exists:
                try:
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
    pool_factory: Callable[[], AsyncConnectionPool],
    idle_seconds: int = 1800,
    check_interval_seconds: int = 60,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Background task: mark idle sessions ended every check_interval_seconds.

    Two-stage eviction from _session_client_info (Task 6 — memory bound):

    Stage 1 — DB-driven:
        Sessions marked ended (ended_at IS NULL → ended_at = now()) are
        returned by the RETURNING clause.  Their cache entries are evicted
        immediately so the dict cannot grow with orphaned entries.

    Stage 2 — TTL sweep:
        Evicts cache entries whose inserted_at is older than idle_seconds
        regardless of DB state.  Guards against sessions that started but
        never made a tool call (so no mcp_session row exists to reap).

    Shutdown:
        Exits cleanly when shutdown_event is set or the coroutine is cancelled.
    """
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
                # Stage 1: evict cache entries for sessions reaped from the DB.
                reaped_ids = [row[0] for row in rows]
                for sid in reaped_ids:
                    _session_client_info.pop(sid, None)
                log.info("sessions_reaped_idle", count=len(rows))
            # Stage 2: TTL sweep — evict stale cache entries that have no
            # corresponding DB row (e.g. session initialised but never called
            # a tool, so mcp_session was never inserted).
            cutoff = time.monotonic() - idle_seconds
            stale = [
                sid for sid, info in _session_client_info.items()
                if info.get("inserted_at", float("inf")) < cutoff
            ]
            for sid in stale:
                _session_client_info.pop(sid, None)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("idle_reaper_error", error=str(exc))


async def mark_sessions_shutdown(pool: AsyncConnectionPool) -> None:
    """Mark all open sessions as ended with reason='shutdown'.

    Called during FastAPI lifespan teardown.  Also clears _session_client_info
    entirely (Task 6) so no stale entries linger after the process restarts.
    """
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
    finally:
        # Clear regardless of DB success so the cache is always consistent
        # with the next process startup (Task 6).
        _session_client_info.clear()
