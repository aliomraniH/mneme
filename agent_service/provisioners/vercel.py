"""Vercel Postgres provisioner.

Creates Neon-backed Postgres databases via the Vercel REST API.
Requires a Vercel API token with Storage write permissions.

Supported regions (Vercel uses Neon region codes):
  iad1  — US East (Washington D.C.)
  sfo1  — US West (San Francisco)
  fra1  — Europe West (Frankfurt)
  sin1  — Asia Pacific (Singapore)
  hnd1  — Asia Pacific (Tokyo)
  cle1  — US East (Cleveland)
  pdx1  — US West (Portland)
  gru1  — South America (São Paulo)
"""

from __future__ import annotations

import httpx
from pydantic import SecretStr

from agent_service.errors import ProvisionError
from agent_service.provisioners.base import ProvisionResult

_API_BASE = "https://api.vercel.com"
_DEFAULT_REGION = "iad1"
_REGIONS = ["iad1", "sfo1", "fra1", "sin1", "hnd1", "cle1", "pdx1", "gru1"]


class VercelPostgresProvisioner:
    """Creates Vercel Postgres databases via the Vercel REST API."""

    def __init__(
        self,
        api_token: SecretStr,
        team_id: str | None = None,
    ) -> None:
        self._token = api_token
        self._team_id = team_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _query_params(self) -> dict[str, str]:
        return {"teamId": self._team_id} if self._team_id else {}

    async def provision(
        self,
        name: str,
        region: str | None = None,
    ) -> ProvisionResult:
        region = region or _DEFAULT_REGION
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{_API_BASE}/v1/storage/postgres",
                headers=self._headers(),
                params=self._query_params(),
                json={"name": name, "region": region},
            )

        if not resp.is_success:
            raise ProvisionError(
                f"Vercel API returned {resp.status_code}: {resp.text[:500]}"
            )

        store = resp.json().get("store", {})
        conn_url = store.get("connectionString") or _build_dsn(store)

        return ProvisionResult(
            provider="vercel",
            database_name=name,
            namespace=name.replace("-", "_"),
            connection_url=conn_url,
            host=store.get("host") or _host_from_dsn(conn_url),
            port=5432,
            database=store.get("database") or name,
            username=store.get("user") or store.get("username") or "",
            region=region,
            provider_id=store.get("id"),
        )

    async def list_regions(self) -> list[str]:
        return list(_REGIONS)


def _build_dsn(store: dict[str, str]) -> str:
    user = store.get("user") or store.get("username") or ""
    pw = store.get("password") or ""
    host = store.get("host") or ""
    db = store.get("database") or ""
    return f"postgres://{user}:{pw}@{host}/{db}?sslmode=require"


def _host_from_dsn(dsn: str) -> str:
    """Best-effort host extraction from a Postgres DSN."""
    try:
        return dsn.split("@", 1)[1].split("/")[0].split(":")[0]
    except (IndexError, AttributeError):
        return ""
