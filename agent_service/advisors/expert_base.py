"""Base class for per-database domain-expert advisors (Phase 2.5).

A domain expert knows the expected schema and access patterns for one specific
namespace.  It supplements the generic advisors (schema_drift, cache_stale,
conflict) with checks that only make sense for that database.

Implementing a new expert
-------------------------
1. Subclass ``DomainExpertAdvisor``.
2. Set ``namespace`` to the exact ``db_namespace`` string the expert covers.
3. Override ``advise`` to return a list of ``Advisory`` objects.
4. Register the class in ``EXPERT_REGISTRY`` at the bottom of this file or in
   the subclass's own module (imported at startup).

Advisory kind
-------------
Domain experts emit advisories with ``kind="domain_expert"``.  The ``metadata``
dict must always include ``"check"`` (a short slug) and ``"namespace"`` so
callers can identify the specific signal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from psycopg_pool import AsyncConnectionPool

from agent_service.models import Advisory


class DomainExpertAdvisor(ABC):
    """Abstract base for per-namespace domain-expert advisors."""

    #: The db_namespace this expert covers.  Must match exactly.
    namespace: str

    @abstractmethod
    async def advise(
        self,
        pool: AsyncConnectionPool,  # type: ignore[type-arg]
        db_namespace: str,
    ) -> list[Advisory]: ...


#: Registry of all known domain experts, keyed by namespace.
#: Populated via register_expert() at import time.
EXPERT_REGISTRY: dict[str, list[DomainExpertAdvisor]] = {}


def register_expert(expert: DomainExpertAdvisor) -> None:
    """Add an expert instance to the global registry."""
    EXPERT_REGISTRY.setdefault(expert.namespace, []).append(expert)


def get_experts(namespace: str) -> list[DomainExpertAdvisor]:
    """Return all registered experts for *namespace* (empty list if none)."""
    return EXPERT_REGISTRY.get(namespace, [])
