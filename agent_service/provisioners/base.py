from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ProvisionResult:
    """Returned by any DatabaseProvisioner.provision() call.

    connection_url is a full Postgres DSN including credentials.
    Treat it as a secret — store in Replit Secrets, never log or commit it.
    """

    provider: str
    database_name: str
    namespace: str       # suggested mneme namespace key (snake_case)
    connection_url: str  # full DSN — store this in Secrets, never log it
    host: str
    port: int
    database: str
    username: str
    region: str | None = None
    provider_id: str | None = None  # provider-assigned resource ID


@runtime_checkable
class DatabaseProvisioner(Protocol):
    """Protocol for managed-Postgres provisioners.

    Each implementation handles a specific cloud provider.  Adding a new
    provider means implementing this protocol — no changes to calling code.
    """

    async def provision(
        self,
        name: str,
        region: str | None = None,
    ) -> ProvisionResult:
        """Create a new Postgres database and return connection details."""
        ...

    async def list_regions(self) -> list[str]:
        """Return the set of supported region identifiers for this provider."""
        ...
