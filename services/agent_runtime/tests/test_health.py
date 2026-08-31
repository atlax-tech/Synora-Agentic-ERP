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


def test_agent_route_is_internal_and_documentation_is_disabled() -> None:
    assert {getattr(route, "path", None) for route in app.routes} == {
        "/healthz",
        "/enhance",
        "/agent/execute",
        "/coach/answer",
        "/workflow/start",
        "/workflow/resume",
        "/workflow/cancel",
        "/workflow/status",
    }
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
