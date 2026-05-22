"""Agent-owned provision_database and list_database_regions tools.

These are native mneme tools (not proxied from an upstream) that allow
Claude Code to create managed Postgres databases at cloud providers and
receive the connection details needed to wire the new database into mneme.

Supported providers
-------------------
vercel   Vercel Postgres (Neon-backed). Requires VERCEL_API_TOKEN in Secrets.
         Optionally set VERCEL_TEAM_ID for team-scoped databases.

Adding a new provider
---------------------
1. Implement DatabaseProvisioner protocol in agent_service/provisioners/<name>.py
2. Add a branch in provision_database's provider dispatch below.
3. Add the API token field(s) to Settings in config.py.
4. Document in .env.example.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastmcp import FastMCP

from agent_service.config import Settings
from agent_service.errors import ProvisionError
from agent_service.provisioners.vercel import _REGIONS as _VERCEL_REGIONS

log = structlog.get_logger(__name__)


def register_provision_tools(mneme: FastMCP, settings: Settings) -> None:
    """Register provisioning tools on the mneme FastMCP server.

    Called from the FastAPI lifespan after settings are fully loaded so the
    tool closures capture the live Settings instance.
    """

    @mneme.tool
    async def provision_database(
        name: str,
        provider: Literal["vercel"],
        region: str | None = None,
    ) -> dict[str, str]:
        """Create a new managed Postgres database at a cloud provider.

        Returns connection details and a next-steps checklist for wiring the
        new database into mneme.  The connection_url contains credentials —
        store it in Replit Secrets immediately; do not commit it anywhere.

        Args:
            name:     Database name.  Use lowercase letters, digits, and hyphens.
                      Max 32 characters.
            provider: Cloud provider.  Currently supported: "vercel".
            region:   Provider region code.  Call list_database_regions first
                      to see available options.  Defaults to provider's primary
                      region (Vercel: "iad1" — US East).
        """
        if provider == "vercel":
            if settings.vercel_api_token is None:
                raise ProvisionError(
                    "VERCEL_API_TOKEN is not set. "
                    "Create a token at https://vercel.com/account/tokens "
                    "(scope: Full Account or specific team with Storage write) "
                    "then add it as VERCEL_API_TOKEN in Replit Secrets."
                )
            from agent_service.provisioners.vercel import VercelPostgresProvisioner

            provisioner = VercelPostgresProvisioner(
                api_token=settings.vercel_api_token,
                team_id=settings.vercel_team_id,
            )
        else:
            raise ProvisionError(
                f"Unknown provider {provider!r}. Supported: 'vercel'."
            )

        result = await provisioner.provision(name=name, region=region)

        log.info(
            "database_provisioned",
            provider=result.provider,
            name=result.database_name,
            namespace=result.namespace,
            region=result.region,
            provider_id=result.provider_id,
        )

        ns = result.namespace
        return {
            "status": "created",
            "provider": result.provider,
            "database_name": result.database_name,
            "suggested_namespace": ns,
            "host": result.host,
            "port": str(result.port),
            "database": result.database,
            "username": result.username,
            "region": result.region or "",
            "provider_id": result.provider_id or "",
            "connection_url": result.connection_url,
            "next_steps": "\n".join([
                f"1. Add to Replit Secrets: DATABASE_URL_{ns.upper()}=<connection_url above>",
                "2. Deploy a DB MCP server for this database (see docs/UPSTREAM.md)",
                f"3. Add to UPSTREAM_DB_MCP_SERVERS: {{\"{ns}\": \"https://your-db-mcp.replit.app/mcp\"}}",
                f"4. Add routing keywords to MNEME_NAMESPACE_ROUTING_KEYWORDS for namespace '{ns}'",
                "5. Restart mneme (Replit → Run) to pick up the new upstream",
            ]),
        }

    @mneme.tool
    async def list_database_regions(
        provider: Literal["vercel"],
    ) -> list[str]:
        """List region codes available for database provisioning at the given provider.

        Pass a region code from this list as the `region` argument when calling
        provision_database.
        """
        if provider == "vercel":
            return list(_VERCEL_REGIONS)
        raise ProvisionError(f"Unknown provider: {provider!r}. Supported: 'vercel'.")
