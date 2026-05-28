from __future__ import annotations

from typing import Protocol, runtime_checkable

from psycopg_pool import AsyncConnectionPool

from agent_service.models import Advisory


@runtime_checkable
class BaseAdvisor(Protocol):
    """Protocol every advisor must satisfy."""

    async def advise(
        self,
        pool: AsyncConnectionPool,  # type: ignore[type-arg]
        db_namespace: str,
    ) -> list[Advisory]: ...
