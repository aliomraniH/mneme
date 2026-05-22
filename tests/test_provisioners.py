"""Unit tests for database provisioners.

Uses monkeypatching on httpx.AsyncClient so no real Vercel API calls are made.

The Vercel provisioner now uses get-or-error semantics:
  1. GET /v1/storage/stores          → list existing stores
  2. GET /v1/storage/stores/{id}/secrets → fetch decrypted connection strings
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agent_service.errors import ProvisionError
from agent_service.provisioners.vercel import VercelPostgresProvisioner

# ── mock response helpers ─────────────────────────────────────────────────────

_STORE_ID = "store_abc123"
_CONN_URL  = "postgres://user:secret@host.neon.tech/my_db?sslmode=require"

def _stores_response(
    name: str = "my-db",
    region: str = "iad1",
    status_code: int = 200,
) -> httpx.Response:
    body: dict[str, Any] = {
        "stores": [
            {
                "id": _STORE_ID,
                "name": name,
                "type": "integration",
                "metadata": {"region": region},
                "product": {"slug": "neon", "tags": ["postgres", "tag_databases"]},
            }
        ]
    }
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )


def _secrets_response(
    name: str = "my-db",
    status_code: int = 200,
) -> httpx.Response:
    body: dict[str, Any] = {
        "data_PGHOST": "host.neon.tech",
        "data_PGUSER": "user",
        "data_PGPASSWORD": "secret",
        "data_PGDATABASE": name.replace("-", "_"),
        "data_POSTGRES_URL": _CONN_URL,
        "data_DATABASE_URL": _CONN_URL,
    }
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )


def _error_response(status_code: int, message: str = "error") -> httpx.Response:
    body = {"error": {"code": "err", "message": message}}
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vercel_provisioner_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy-path: store exists → returns correct ProvisionResult."""

    call_count = 0

    async def _mock_get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if "/secrets" in url:
            return _secrets_response("my-db")
        return _stores_response("my-db", "iad1")

    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get)

    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok_test"))
    result = await provisioner.provision("my-db", region="iad1")

    assert result.provider == "vercel"
    assert result.database_name == "my-db"
    assert result.namespace == "my_db"
    assert result.host == "host.neon.tech"
    assert result.port == 5432
    assert result.region == "iad1"
    assert result.provider_id == _STORE_ID
    assert "sslmode=require" in result.connection_url
    assert call_count == 2   # list + secrets


@pytest.mark.asyncio
async def test_vercel_provisioner_store_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Store name not in list → ProvisionError with dashboard instructions."""

    async def _mock_get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        # Return a store with a *different* name
        return _stores_response("other-db", "iad1")

    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get)

    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok_test"))
    with pytest.raises(ProvisionError, match="No Vercel Neon store named"):
        await provisioner.provision("missing-db")


@pytest.mark.asyncio
async def test_vercel_provisioner_list_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """List-stores API error → ProvisionError with status code."""

    async def _mock_get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        return _error_response(422, "plan limit exceeded")

    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get)

    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok_test"))
    with pytest.raises(ProvisionError, match="422"):
        await provisioner.provision("new-db")


@pytest.mark.asyncio
async def test_vercel_provisioner_team_id_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """teamId query param is forwarded on both list and secrets requests."""
    captured: list[dict[str, object]] = []

    async def _mock_get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        captured.append({"url": url, "params": kwargs.get("params")})
        if "/secrets" in url:
            return _secrets_response("db")
        return _stores_response("db")

    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get)

    provisioner = VercelPostgresProvisioner(
        api_token=SecretStr("tok_test"),
        team_id="team_xyz",
    )
    await provisioner.provision("db")

    assert len(captured) == 2
    for call in captured:
        assert call.get("params") == {"teamId": "team_xyz"}


@pytest.mark.asyncio
async def test_vercel_provisioner_name_slug_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hyphens vs underscores in name are matched equivalently."""

    async def _mock_get(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        if "/secrets" in url:
            return _secrets_response("my-db")
        # Store is stored with hyphens; we query with underscores
        return _stores_response("my-db", "iad1")

    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get)

    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok_test"))
    result = await provisioner.provision("my_db")   # underscore variant
    assert result.database_name == "my-db"
    assert result.namespace == "my_db"


@pytest.mark.asyncio
async def test_vercel_list_regions() -> None:
    """list_regions returns non-empty list of strings including iad1 and fra1."""
    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok"))
    regions = await provisioner.list_regions()
    assert isinstance(regions, list)
    assert "iad1" in regions
    assert "fra1" in regions
