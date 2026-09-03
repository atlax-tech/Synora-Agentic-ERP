"""Runtime /enhance 端点测试: 确定性计划 -> 模型解释增强, 失败回退不抛 5xx。

CI 环境未配置 BYOK provider, provider_from_environment fail closed ->
enhance_plan 回退确定性摘要并返回证据, 端点仍返回 200。
"""

import asyncio
import json

import httpx
from agent_runtime.agent.context import CONTEXT_INPUT_TOKEN_BUDGET_ENV
from agent_runtime.app import app
from agent_runtime.multi_agent.contracts import plan_view_digest, plan_view_from_mapping
from agent_runtime.providers import (
    DeterministicProvider,
    ProviderError,
    ProviderResponse,
    ProviderToolCall,
)

PLAN = {
    "goal": "ensure stock for ITEM-9",
    "company": "Test Company",
    "warehouse": "Main",
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


def test_enhance_planner_reviewer_exposes_only_sanitized_orchestration(monkeypatch) -> None:
    digest = plan_view_digest(plan_view_from_mapping(PLAN))
    selected_roles: list[str] = []
    observed_caps: list[int | None] = []
    provider = DeterministicProvider(
        scripted_responses=[
            ProviderResponse(
                text=json.dumps(
                    {
                        "candidate_explanation": "该物料库存 2.0，建议补货。",
                        "citation_summary": ["risk=SHORTAGE"],
                        "unknowns": [],
                        "plan_digest": digest,
                    },
                    ensure_ascii=False,
                ),
                prompt_tokens=2,
                completion_tokens=3,
            ),
            ProviderResponse(
                text=json.dumps(
                    {
                        "decision": "ACCEPT",
                        "issue_codes": [],
                        "feedback": "",
                        "reviewed_plan_digest": digest,
                    }
                ),
                prompt_tokens=2,
                completion_tokens=2,
            ),
        ]
    )

    def selected_provider(role: str, *, environ=None):
        del environ
        selected_roles.append(role)
        return provider

    from agent_runtime import app as runtime_app

    original_runner = runtime_app.run_planner_reviewer

    async def observed_runner(*args, **kwargs):
        observed_caps.append(kwargs.get("max_completion_tokens"))
        return await original_runner(*args, **kwargs)

    monkeypatch.setenv("ASSIST_MODEL", "glm-5.3-flash")
    monkeypatch.setattr("agent_runtime.app.provider_for_role", selected_provider)
    monkeypatch.setattr(runtime_app, "run_planner_reviewer", observed_runner)

    response = asyncio.run(
        _post_enhance(
            {
                "plan": PLAN,
                "orchestration_mode": "planner_reviewer",
                "orchestration_scope": {
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "run_id": "00000000-0000-0000-0000-000000000002",
                    "correlation_id": "00000000-0000-0000-0000-000000000003",
                    "principal": "buyer@example.test",
                    "company": "Test Company",
                    "warehouse": "Main",
                },
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["explanation"] == "该物料库存 2.0，建议补货。"
    assert selected_roles == ["assist"]
    assert body["evidence"]["provider"] == "glm-5.3-flash"
    orchestration = body["evidence"]["orchestration"]
    assert orchestration["mode"] == "planner_reviewer"
    assert orchestration["model_calls"] == 2
    assert orchestration["handoff_count"] == 1
    assert orchestration["trace"]["digest"]
    assert "candidate_explanation" not in json.dumps(orchestration)
    assert observed_caps == [512]


def test_enhance_rejects_unknown_orchestration_mode() -> None:
    response = asyncio.run(_post_enhance({"plan": PLAN, "orchestration_mode": "supervisor"}))
    assert response.status_code == 422


def test_enhance_planner_reviewer_requires_scope() -> None:
    response = asyncio.run(_post_enhance({"plan": PLAN, "orchestration_mode": "planner_reviewer"}))
    assert response.status_code == 422


def test_enhance_invalid_planner_projection_uses_safe_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_runtime.app.provider_for_role",
        lambda role, *, environ=None: (_ for _ in ()).throw(ProviderError("secret=top-secret")),
    )
    response = asyncio.run(
        _post_enhance(
            {
                "plan": {
                    "summary": "purchase.submit secret: abc",
                    "findings": [],
                },
                "orchestration_mode": "planner_reviewer",
                "orchestration_scope": {
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "run_id": "00000000-0000-0000-0000-000000000002",
                    "correlation_id": "00000000-0000-0000-0000-000000000003",
                    "principal": "buyer@example.test",
                    "company": "Test Company",
                    "warehouse": "Main",
                },
            }
        )
    )
    assert response.status_code == 200
    assert response.json()["explanation"] == "无法生成计划解释，请人工核对确定性计划。"


def test_enhance_provider_failure_does_not_leak_cross_scope_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_runtime.app.provider_for_role",
        lambda role, *, environ=None: (_ for _ in ()).throw(ProviderError("not configured")),
    )
    plan = {**PLAN, "company": "Secret Company", "summary": "跨范围敏感库存 777.0"}
    response = asyncio.run(
        _post_enhance(
            {
                "plan": plan,
                "orchestration_mode": "planner_reviewer",
                "orchestration_scope": {
                    "task_id": "00000000-0000-0000-0000-000000000001",
                    "run_id": "00000000-0000-0000-0000-000000000002",
                    "correlation_id": "00000000-0000-0000-0000-000000000003",
                    "principal": "buyer@example.test",
                    "company": "Allowed Company",
                    "warehouse": "Main",
                },
            }
        )
    )
    assert response.status_code == 200
    assert response.json()["explanation"] == "无法生成计划解释，请人工核对确定性计划。"
    assert "Secret Company" not in response.text
    assert "777.0" not in response.text
    assert "top-secret" not in response.text


def test_enhance_rejects_secret_like_provider_name_without_echoing_it() -> None:
    response = asyncio.run(
        _post_enhance(
            {
                "plan": PLAN,
                "provider_name": "api_key=TOPSECRET",
            }
        )
    )
    assert response.status_code == 200
    assert response.json()["evidence"]["provider"] == "untrusted-provider"
    assert "TOPSECRET" not in response.text


def test_enhance_provider_error_does_not_echo_secret_in_single_agent_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_runtime.app.provider_from_environment",
        lambda *, environ=None: (_ for _ in ()).throw(ProviderError("api_key=TOPSECRET")),
    )
    response = asyncio.run(_post_enhance({"plan": PLAN, "provider_name": "ci-test"}))
    assert response.status_code == 200
    assert response.json()["evidence"]["fallback_reason"] == "provider not configured"
    assert "TOPSECRET" not in response.text


def test_enhance_single_agent_unsafe_summary_falls_back_to_constant(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_runtime.app.provider_from_environment",
        lambda *, environ=None: (_ for _ in ()).throw(
            ProviderError("secret=TOPSECRET", failure_code="TRANSPORT_ERROR")
        ),
    )
    response = asyncio.run(
        _post_enhance(
            {
                "plan": {"summary": "purchase.submit secret: TOPSECRET", "findings": []},
                "provider_name": "ci-test",
            }
        )
    )
    assert response.status_code == 200
    assert response.json()["explanation"] == "无法生成计划解释，请人工核对确定性计划。"
    assert "TOPSECRET" not in response.text


def test_enhance_single_agent_evidence_counts_tool_calls(monkeypatch) -> None:
    monkeypatch.setenv(CONTEXT_INPUT_TOKEN_BUDGET_ENV, "100000")

    class _ToolProvider:
        async def complete(self, *args, **kwargs):
            del args, kwargs
            return ProviderResponse(
                text="库存 2.0，建议补货。",
                tool_calls=(ProviderToolCall(id="1", name="purchase.submit", arguments="{}"),),
                prompt_tokens=2,
                completion_tokens=3,
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "agent_runtime.app.provider_from_environment", lambda *, environ=None: _ToolProvider()
    )
    response = asyncio.run(_post_enhance({"plan": PLAN, "provider_name": "ci-test"}))
    assert response.status_code == 200
    assert response.json()["evidence"]["unauthorized_tool_calls"] == 1
    assert response.json()["explanation"] == PLAN["summary"]
