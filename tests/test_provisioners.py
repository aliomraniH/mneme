"""Unit tests for database provisioners.

Uses httpx's MockTransport so no real Vercel API calls are made.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from agent_service.errors import ProvisionError
from agent_service.provisioners.vercel import VercelPostgresProvisioner


def _mock_vercel_response(
    name: str = "test-db",
    region: str = "iad1",
    status_code: int = 200,
    body: dict | None = None,
) -> httpx.Response:
    if body is None:
        body = {
            "store": {
                "id": "store_abc123",
                "name": name,
                "region": region,
                "connectionString": f"postgres://user:secret@host.neon.tech/{name}?sslmode=require",
                "host": "host.neon.tech",
                "database": name,
                "user": "user",
                "password": "secret",
            }
        }
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )


@pytest.mark.asyncio
async def test_vercel_provisioner_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy-path: API returns 200 with store payload."""

    async def _mock_post(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        return _mock_vercel_response("my-db", "iad1")

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post)

    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok_test"))
    result = await provisioner.provision("my-db", region="iad1")

    assert result.provider == "vercel"
    assert result.database_name == "my-db"
    assert result.namespace == "my_db"
    assert result.host == "host.neon.tech"
    assert result.port == 5432
    assert result.region == "iad1"
    assert result.provider_id == "store_abc123"
    assert "sslmode=require" in result.connection_url


@pytest.mark.asyncio
async def test_vercel_provisioner_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """API 422 → ProvisionError with status code in message."""

    async def _mock_post(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        return _mock_vercel_response(status_code=422, body={"error": "name already taken"})

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post)

    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok_test"))
    with pytest.raises(ProvisionError, match="422"):
        await provisioner.provision("existing-db")


@pytest.mark.asyncio
async def test_vercel_provisioner_team_id_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """teamId query param is forwarded when VERCEL_TEAM_ID is set."""
    captured: dict[str, object] = {}

    async def _mock_post(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        captured.update({"params": kwargs.get("params"), "url": url})
        return _mock_vercel_response()

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post)

    provisioner = VercelPostgresProvisioner(
        api_token=SecretStr("tok_test"),
        team_id="team_xyz",
    )
    await provisioner.provision("db")
    assert captured.get("params") == {"teamId": "team_xyz"}


@pytest.mark.asyncio
async def test_vercel_list_regions() -> None:
    """list_regions returns non-empty list of strings."""
    provisioner = VercelPostgresProvisioner(api_token=SecretStr("tok"))
    regions = await provisioner.list_regions()
    assert isinstance(regions, list)
    assert "iad1" in regions
    assert "fra1" in regions
