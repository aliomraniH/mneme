from __future__ import annotations

import time
from collections.abc import Callable
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

    Constructed at module scope with a pool_factory callable so the
    actual pool can be deferred to the FastAPI lifespan.

    Failures inside the middleware are logged and silently dropped — the
    call always passes through.
    """

    def __init__(self, pool_factory: Callable[[], AsyncConnectionPool]) -> None:  # type: ignore[type-arg]
        self._pool_factory = pool_factory

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name: str = context.message.name
        params: dict[str, Any] = context.message.arguments or {}
        db_namespace = route_to_namespace(tool_name, params)

        # Pre-generate audit_id so it's available in both success and error paths
        audit_id: UUID = uuid4()

        # Capture caller metadata from MCP context
        fastmcp_ctx = context.fastmcp_context
        session_id: str | None = None
        client_name: str | None = None
        client_version: str | None = None
        client_ip: str | None = None
        user_agent_str: str | None = None

        if fastmcp_ctx is not None:
            try:
                session_id = fastmcp_ctx.session_id
            except RuntimeError:
                pass
            try:
                from fastmcp.server.dependencies import get_http_request

                req = get_http_request()
                client_ip = req.headers.get("x-forwarded-for") or (
                    req.client.host if req.client else None
                )
                user_agent_str = req.headers.get("user-agent")
            except Exception:
                pass

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
            row_count: int | None = (
                len(result_content) if isinstance(result_content, list) else None
            )
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

            try:
                pool = self._pool_factory()
                await write_episode(pool, episode)
            except Exception as write_exc:
                log.warning(
                    "audit_write_failed",
                    tool_name=tool_name,
                    error=str(write_exc),
                    audit_id=str(audit_id),
                )

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

        # Reached only on success (exception path re-raises before here)
        assert result is not None  # always true on success path
        existing_meta: dict[str, Any] = dict(result.meta or {})
        existing_meta["audit_id"] = str(audit_id)
        return result.model_copy(update={"meta": existing_meta})


def _extract_content(result: ToolResult) -> Any:
    """Pull a JSON-serializable digest from a ToolResult."""
    if result.structured_content is not None:
        return result.structured_content
    items = []
    for block in result.content:
        if hasattr(block, "text"):
            items.append(block.text)
        else:
            items.append(str(block))
    return items if len(items) != 1 else items[0]
