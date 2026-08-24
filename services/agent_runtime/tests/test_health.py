import asyncio

import httpx
from agent_runtime.app import app


async def _get_health() -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get("/healthz")


def test_health_endpoint() -> None:
    response = asyncio.run(_get_health())

    assert response.status_code == 200
    assert response.json() == {"service": "synora-agent-runtime", "status": "ok"}


def test_health_is_the_only_http_route() -> None:
    assert {route.path for route in app.routes} == {"/healthz"}
