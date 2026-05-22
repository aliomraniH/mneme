from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import mcp.types as mt
import structlog
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext, Middleware
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 30.0


class TimeoutMiddleware(Middleware):
    """Enforces a per-call timeout on tool invocations.

    On timeout: logs the event, returns a clean MCP error to the caller.
    The audit middleware (which wraps this one) still writes a query_episode
    row with error='timeout_30s'.
    """

    def __init__(self, timeout_seconds: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout_seconds

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        try:
            return await asyncio.wait_for(
                call_next(context),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "tool_call_timeout",
                tool_name=tool_name,
                timeout_seconds=self._timeout,
            )
            raise McpError(
                error=ErrorData(
                    code=-32000,
                    message=f"Tool '{tool_name}' timed out after {self._timeout:.0f}s",
                )
            )
