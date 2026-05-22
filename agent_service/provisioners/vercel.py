"""Vercel Neon-integration provisioner.

Vercel retired the legacy "Vercel Postgres" product (POST /v1/storage/postgres)
in 2024.  Postgres databases are now Neon integration stores created through the
Vercel dashboard (Storage → Create Database → Neon).  Once a store exists, its
connection details are available via the Vercel REST API.

This provisioner therefore uses get-or-error semantics:
  • If a Neon store whose name matches `name` already exists → return its
    connection details (status: "existing").
  • If no matching store is found → raise ProvisionError with instructions to
    create one in the Vercel dashboard first, then call provision_database again.

Supported regions (Vercel / Neon region codes):
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

from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from agent_service.errors import ProvisionError
from agent_service.provisioners.base import ProvisionResult

_API_BASE = "https://api.vercel.com"
_DEFAULT_REGION = "iad1"
_REGIONS = ["iad1", "sfo1", "fra1", "sin1", "hnd1", "cle1", "pdx1", "gru1"]


class VercelPostgresProvisioner:
    """Returns connection details for an existing Vercel / Neon Postgres store."""

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

    def _params(self) -> dict[str, str]:
        return {"teamId": self._team_id} if self._team_id else {}

    async def _list_stores(self, client: httpx.AsyncClient) -> list[dict]:
        """Return all Neon/Postgres integration stores on the account."""
        resp = await client.get(
            f"{_API_BASE}/v1/storage/stores",
            headers=self._headers(),
            params=self._params(),
        )
        if not resp.is_success:
            raise ProvisionError(
                f"Vercel API error listing stores ({resp.status_code}): {resp.text[:400]}"
            )
        stores = resp.json().get("stores", [])
        # Keep only Neon / Postgres stores (type == "integration" with postgres tags,
        # or legacy type == "postgres").
        return [
            s for s in stores
            if s.get("type") in ("postgres", "integration")
            and _store_is_postgres(s)
        ]

    async def _get_secrets(
        self, client: httpx.AsyncClient, store_id: str
    ) -> dict[str, str]:
        """Fetch decrypted secret values for a store."""
        resp = await client.get(
            f"{_API_BASE}/v1/storage/stores/{store_id}/secrets",
            headers=self._headers(),
            params=self._params(),
        )
        if not resp.is_success:
            raise ProvisionError(
                f"Vercel API error reading store secrets ({resp.status_code}): {resp.text[:400]}"
            )
        raw = resp.json()
        # Values are keyed as "data_<VAR_NAME>"
        return {k[5:]: v for k, v in raw.items() if k.startswith("data_")}

    async def provision(
        self,
        name: str,
        region: str | None = None,
    ) -> ProvisionResult:
        """Return connection details for a Vercel Neon store named `name`.

        The Vercel dashboard API no longer exposes a programmatic "create"
        endpoint for Neon stores.  If the named store does not exist yet,
        this method raises ProvisionError with instructions to create it first.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            stores = await self._list_stores(client)

        # Match by exact name first, then by slug/namespace equivalence
        # (e.g. "test-mneme-db" ↔ "test_mneme_db").
        slug = name.replace("-", "_").lower()
        match: dict | None = None
        for s in stores:
            store_name = s.get("name", "")
            if store_name == name or store_name.replace("-", "_").lower() == slug:
                match = s
                break

        if match is None:
            store_names = [s.get("name", "") for s in stores]
            available = (
                f"Available stores: {store_names}" if store_names else "No Postgres stores found."
            )
            raise ProvisionError(
                f"No Vercel Neon store named {name!r} found on this account.\n"
                f"{available}\n\n"
                "To create one:\n"
                "  1. Go to https://vercel.com/dashboard → Storage → Create Database → Neon\n"
                "  2. Name it exactly as requested and choose your region\n"
                "  3. Call provision_database again with that name — this tool will then\n"
                "     return its connection details automatically."
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            secrets = await self._get_secrets(client, match["id"])

        conn_url = (
            secrets.get("POSTGRES_URL")
            or secrets.get("DATABASE_URL")
            or secrets.get("POSTGRES_URL_NON_POOLING")
            or ""
        )
        conn_url_unpooled = (
            secrets.get("DATABASE_URL_UNPOOLED")
            or secrets.get("POSTGRES_URL_NON_POOLING")
            or conn_url
        )
        host = secrets.get("PGHOST") or secrets.get("POSTGRES_HOST") or _host_from_dsn(conn_url)
        db   = secrets.get("PGDATABASE") or secrets.get("POSTGRES_DATABASE") or "neondb"
        user = secrets.get("PGUSER") or secrets.get("POSTGRES_USER") or ""
        region_actual = (match.get("metadata") or {}).get("region") or region or _DEFAULT_REGION

        return ProvisionResult(
            provider="vercel",
            database_name=match.get("name", name),
            namespace=match.get("name", name).replace("-", "_"),
            connection_url=conn_url,
            host=host,
            port=5432,
            database=db,
            username=user,
            region=region_actual,
            provider_id=match.get("id"),
        )

    async def list_regions(self) -> list[str]:
        return list(_REGIONS)


# ── helpers ──────────────────────────────────────────────────────────────────

def _store_is_postgres(store: dict) -> bool:
    """Return True if this store is a Postgres / Neon database."""
    product = store.get("product") or {}
    tags = product.get("tags") or []
    slug = product.get("slug") or ""
    return "postgres" in tags or slug in ("neon", "postgres") or store.get("type") == "postgres"


def _host_from_dsn(dsn: str) -> str:
    try:
        return urlparse(dsn).hostname or ""
    except Exception:
        return ""
