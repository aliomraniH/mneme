from __future__ import annotations

from typing import Any, Protocol

from agent_service.models import Advisory, Episode, ToolCall


class ExpertAgent(Protocol):
    """One per db_namespace. Phase 2.5 adds PostgresExpert, PineconeExpert, etc."""

    db_namespace: str

    async def advise(self, call: ToolCall, ctx: Any) -> list[Advisory]: ...

    async def remember(self, episode: Episode) -> None: ...

    async def recall(self, query: str, k: int) -> list[Episode]: ...


class Router(Protocol):
    """Routes a ToolCall to the appropriate ExpertAgent."""

    async def route(self, call: ToolCall) -> ExpertAgent: ...
