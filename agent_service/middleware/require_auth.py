from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class RequireAuthMiddleware:
    """No-op authentication middleware for Phase 1.

    Phase 2 will flip this on without refactoring callers — just replace
    the pass-through body with actual token validation.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        # Phase 1: unconditional pass-through.
        await self.app(scope, receive, send)
