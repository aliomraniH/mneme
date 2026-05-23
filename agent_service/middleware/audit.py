from __future__ import annotations

import time
import uuid as _uuid
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

import mcp.types as mt
import structlog
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext, Middleware
from fastmcp.tools.base import ToolResult
from psycopg_pool import AsyncConnectionPool

from agent_service.memory.episodes import write_episode
from agent_service.models import Episode, truncate_result_summary
from agent_service.routing import route_to_namespace

log = structlog.get_logger(__name__)


class AuditMiddleware(Middleware):
    """Intercepts every tool call, writes one query_episode row, and injects
    meta.audit_id into the response.

    Constructed at module scope with a pool_factory callable so the actual pool
    can be deferred to the FastAPI lifespan.  namespace_keywords_factory mirrors
    the same pattern: when set it returns per-namespace routing keyword overrides
    loaded from config; when None, routing.py's built-in defaults are used.

    trusted_proxy_hops (Task 10):
        Forwarded to _resolve_client_ip() for every call.  Defaults to 0 (use
        TCP peer address).  Set to N if mneme sits behind exactly N trusted
        reverse proxies.

    Failures inside the middleware are logged and silently dropped — the
    call always passes through.  write_episode() never raises (Task 9), so
    the redundant outer try/except that previously wrapped it has been removed.
    """

    def __init__(
        self,
        pool_factory: Callable[[], AsyncConnectionPool],
        namespace_keywords_factory: (
            Callable[[], dict[str, list[str]] | None] | None
        ) = None,
        # Task 10: accept the configured hop count so client_ip resolution
        # is consistent across all callers.  Wired from get_settings() in
        # server.py so the value comes from the TRUSTED_PROXY_HOPS env var.
        trusted_proxy_hops: int = 0,
    ) -> None:
        self._pool_factory = pool_factory
        self._namespace_keywords_factory = namespace_keywords_factory
        self._trusted_proxy_hops = trusted_proxy_hops

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name: str = context.message.name
        params: dict[str, Any] = context.message.arguments or {}
        kws = (
            self._namespace_keywords_factory()
            if self._namespace_keywords_factory is not None
            else None
        )
        db_namespace = route_to_namespace(tool_name, params, namespace_keywords=kws)

        # Pre-generate audit_id so it is available in both success and error paths.
        audit_id: UUID = uuid4()

        # ── Capture caller metadata ──────────────────────────────────────────
        fastmcp_ctx = context.fastmcp_context
        session_id: str | None = None
        client_name: str | None = None
        client_version: str | None = None
        client_ip: str | None = None
        user_agent_str: str | None = None

        if fastmcp_ctx is not None:
            with suppress(Exception):
                from fastmcp.server.dependencies import get_http_request

                # Import _resolve_client_ip from session so the resolution
                # logic lives in exactly one place (Task 10).
                from agent_service.middleware.session import _resolve_client_ip

                req = get_http_request()
                _xff = req.headers.get("x-forwarded-for")
                _peer = req.client.host if req.client else None
                # Task 10: use the configured hop count, not a bare XFF read.
                client_ip = _resolve_client_ip(
                    xff=_xff,
                    peer_host=_peer,
                    trusted_proxy_hops=self._trusted_proxy_hops,
                )
                user_agent_str = req.headers.get("user-agent")
                # Use the HTTP transport session ID as the FK into mcp_session.
                # Falls back to fastmcp_ctx.session_id for non-HTTP transports.
                raw_sid = req.headers.get("mcp-session-id")
                if raw_sid:
                    try:
                        session_id = str(_uuid.UUID(raw_sid))
                    except ValueError:
                        session_id = raw_sid
            if session_id is None:
                with suppress(RuntimeError):
                    session_id = fastmcp_ctx.session_id

            # Task 5: read client_name / client_version from the persistent
            # session cache populated by SessionMiddleware after the first
            # successful INSERT.  This avoids re-querying the DB on every call
            # and works for all calls after the first (when the MCP protocol
            # no longer sends clientInfo in the request).
            if session_id is not None:
                with suppress(Exception):
                    from agent_service.middleware.session import get_session_client_info

                    info = get_session_client_info(session_id)
                    if info is not None:
                        client_name = info.get("client_name")
                        client_version = info.get("client_version")

        start = time.monotonic()
        error_msg: str | None = None
        result_content: Any = None
        result: ToolResult | None = None

        try:
            result = await call_next(context)
            result_content = _extract_content(result)
        except Exception as exc:
            error_msg = str(exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)

            # Task 4: row_count population — three-way branch.
            #
            # Before this fix, row_count was always NULL for query calls
            # because db_mcp returns a dict shaped {"row_count": N, "rows": [...],
            # "truncated": bool}, not a list.  The old code only handled the
            # list case (content blocks from text tool results).
            #
            #   list  → number of content blocks (e.g. text results)
            #   dict  → read "row_count" key (db_mcp structured query result)
            #   else  → NULL (unknown structure, don't guess)
            if isinstance(result_content, list):
                row_count: int | None = len(result_content)
            elif isinstance(result_content, dict):
                rc = result_content.get("row_count")
                row_count = int(rc) if isinstance(rc, int) else None
            else:
                row_count = None

            summary, was_truncated = truncate_result_summary(result_content)

            episode = Episode(
                audit_id=audit_id,
                db_namespace=db_namespace,
                tool_name=tool_name,
                tool_params=params,
                result_summary=summary if error_msg is None else None,
                row_count=row_count,
                duration_ms=duration_ms,
                error=error_msg,
                source="error" if error_msg is not None else "ok",
                session_id=session_id,
                client_name=client_name,
                client_version=client_version,
                client_ip=client_ip,
                user_agent=user_agent_str,
                truncated=was_truncated,
            )

            pool = self._pool_factory()
            # Task 9: write_episode never raises (it catches internally and
            # logs a warning).  The redundant outer try/except that previously
            # wrapped this call has been removed — it was masking real errors
            # and making the contract unclear.
            await write_episode(pool, episode)

            log.info(
                "tool_call",
                tool_name=tool_name,
                db_namespace=db_namespace,
                duration_ms=duration_ms,
                error=error_msg,
                audit_id=str(audit_id),
                session_id=session_id,
                truncated=was_truncated,
            )

        # Reached only on success (exception path re-raises before here).
        assert result is not None  # always true on success path
        existing_meta: dict[str, Any] = dict(result.meta or {})
        existing_meta["audit_id"] = str(audit_id)
        return result.model_copy(update={"meta": existing_meta})


def _extract_content(result: ToolResult) -> Any:
    """Pull a JSON-serializable digest from a ToolResult.

    Preference order:
      1. structured_content — db_mcp returns {"row_count": N, "rows": [...]}
         here; this is what AuditMiddleware uses for row_count (Task 4).
      2. content blocks — fallback for plain-text tool results.
    """
    if result.structured_content is not None:
        return result.structured_content
    items = []
    for block in result.content:
        if hasattr(block, "text"):
            items.append(block.text)
        else:
            items.append(str(block))
    return items if len(items) != 1 else items[0]
