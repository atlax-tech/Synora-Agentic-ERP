"""Runtime /enhance 端点测试: 确定性计划 -> 模型解释增强, 失败回退不抛 5xx。

CI 环境未配置 BYOK provider, provider_from_environment fail closed ->
enhance_plan 回退确定性摘要并返回证据, 端点仍返回 200。
"""

import asyncio

import httpx
from agent_runtime.app import app

PLAN = {
    "summary": "共分析 1 个物料：1 个缺货、0 个重复采购风险。",
    "findings": [
        {
            "item_code": "ITEM-9",
            "risk": "SHORTAGE",
            "recommendation": "建议补货 ITEM-9：库存 2.0 + 在途 0.0 - 需求 10.0 = -8.0 < 0。",
            "evidence": ["risk=SHORTAGE", "shortage=8.0"],
            "matched_goal": True,
        }
    ],
    "generated_at": "2026-08-25T21:00:00+08:00",
}


async def _post_enhance(
    payload: dict[str, object], headers: dict[str, str] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.post("/enhance", json=payload, headers=headers)


def test_enhance_returns_fallback_when_provider_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("SYNORA_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("SYNORA_PROVIDER_API_KEY", raising=False)
    response = asyncio.run(_post_enhance({"plan": PLAN, "provider_name": "ci-test"}))
    assert response.status_code == 200  # 回退不是 5xx
    body = response.json()
    assert body["explanation"] == PLAN["summary"]  # 回退确定性摘要
    assert body["evidence"]["status"] == "fallback_error"
    assert body["evidence"]["fallback_reason"] is not None
    assert "provider not configured" in body["evidence"]["fallback_reason"]


def test_enhance_rejects_invalid_payload() -> None:
    response = asyncio.run(_post_enhance({"plan": "not-a-dict"}))
    assert response.status_code == 422


def test_enhance_requires_configured_runtime_token(monkeypatch) -> None:
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", "test-runtime-token")
    response = asyncio.run(_post_enhance({"plan": PLAN, "provider_name": "ci-test"}))
    assert response.status_code == 401


def test_enhance_accepts_configured_runtime_token(monkeypatch) -> None:
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", "test-runtime-token")
    monkeypatch.delenv("SYNORA_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("SYNORA_PROVIDER_API_KEY", raising=False)
    response = asyncio.run(
        _post_enhance(
            {"plan": PLAN, "provider_name": "ci-test"},
            headers={"X-Synora-Runtime-Token": "test-runtime-token"},
        )
    )
    assert response.status_code == 200
    assert response.json()["evidence"]["provider"] == "ci-test"


def test_enhance_context_budget_failure_is_a_deterministic_200_fallback(monkeypatch) -> None:
    class _UnexpectedProvider:
        calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

        async def aclose(self) -> None:
            return None

    provider = _UnexpectedProvider()
    monkeypatch.setenv("SYNORA_PROVIDER_MODEL", "recorded")
    monkeypatch.delenv("SYNORA_CONTEXT_INPUT_TOKEN_BUDGET", raising=False)
    monkeypatch.setattr(
        "agent_runtime.app.provider_from_environment",
        lambda *, environ=None: provider,
    )

    response = asyncio.run(_post_enhance({"plan": PLAN, "provider_name": "ci-test"}))

    assert response.status_code == 200
    assert response.json()["explanation"] == PLAN["summary"]
    assert response.json()["evidence"]["status"] == "fallback_context_budget"
    assert provider.calls == 0
