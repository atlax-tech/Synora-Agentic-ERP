"""P4.5 internal Agent endpoint and capability-backed Gateway adapter tests."""

import asyncio
import json
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from agent_runtime.agent.budget import Pricing
from agent_runtime.agent.contracts import Action, observation_from_summary
from agent_runtime.agent.execution import (
    AgentExecuteRequest,
    GatewayToolAdapter,
    _bounded_summary,
    execute_agent,
)
from agent_runtime.app import app
from agent_runtime.gateway import GatewayRequest, GatewaySuccess
from agent_runtime.providers import (
    DeterministicProvider,
    ProviderResponse,
    ProviderToolCall,
)
from pydantic import SecretStr, ValidationError

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")
CAPABILITY = "A" * 43


def _success(tool_name: str, data: list[dict[str, object]]) -> GatewaySuccess:
    return GatewaySuccess.model_validate(
        {
            "ok": True,
            "schema_version": "1",
            "run_id": str(RUN_ID),
            "state_version": 3,
            "correlation_id": str(CORRELATION_ID),
            "tool": {
                "name": tool_name,
                "version": "1",
                "risk": "READ",
                "caller_authorization": "FRAPPE_PERMISSION_AND_RUN_SCOPE",
                "timeout_ms": 5000,
                "max_page_size": 50,
            },
            "authorized_scope": {"company": "Acme", "warehouse": "Stores - A"},
            "snapshot": {
                "captured_at": "2026-08-26 10:00:00",
                "source_modified_at": None,
                "frappe_revision": "f" * 40,
                "erpnext_revision": "e" * 40,
            },
            "completeness": {"status": "COMPLETE", "omissions": {}},
            "page": {"offset": 0, "limit": 20, "returned": len(data), "has_more": False},
            "data": data,
        }
    )


class _FakeGatewayClient:
    def __init__(self, responses: dict[str, GatewaySuccess]) -> None:
        self.responses = responses
        self.requests: list[GatewayRequest] = []
        self.closed = False

    async def execute(self, request: GatewayRequest) -> GatewaySuccess:
        self.requests.append(request)
        return self.responses[request.tool.name]

    async def aclose(self) -> None:
        self.closed = True


class _ClosableProvider(DeterministicProvider):
    def __init__(self, responses: list[ProviderResponse]) -> None:
        super().__init__(scripted_responses=responses)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _request_payload() -> dict[str, str]:
    return {
        "run_id": str(RUN_ID),
        "correlation_id": str(CORRELATION_ID),
        "goal": "ensure stock for ITEM-1",
        "capability": CAPABILITY,
    }


def test_agent_request_validates_and_hides_capability() -> None:
    request = AgentExecuteRequest.model_validate(_request_payload())

    assert request.capability.get_secret_value() == CAPABILITY
    assert CAPABILITY not in repr(request)
    with pytest.raises(ValidationError):
        AgentExecuteRequest.model_validate({**_request_payload(), "unexpected": True})
    with pytest.raises(ValidationError):
        AgentExecuteRequest.model_validate({**_request_payload(), "capability": "short"})


def test_gateway_adapter_sends_capability_only_to_typed_gateway_and_bounds_summary() -> None:
    success = _success("item.lookup", [{"item_code": "ITEM-1", "item_name": "Bearing"}])
    client = _FakeGatewayClient({"item.lookup": success})
    adapter = GatewayToolAdapter(
        client=client,
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        capability=SecretStr(CAPABILITY),
    )
    action = Action(
        step=1,
        tool_name="item.lookup",
        canonical_args={"query": "ITEM-1"},
        correlation_id=CORRELATION_ID,
    )

    observation = asyncio.run(adapter.execute(action))

    assert observation.ok is True
    assert len(observation.summary) <= 3_800
    assert client.requests[0].capability.get_secret_value() == CAPABILITY
    assert CAPABILITY not in repr(observation)
    asyncio.run(adapter.aclose())
    assert client.closed is True


def test_execute_agent_observation_drives_second_different_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _success("item.lookup", [{"item_code": "ITEM-1"}])
    stock = _success("stock.projected", [{"item_code": "ITEM-1", "actual_qty": 60}])
    item_digest = observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name="item.lookup",
        ok=True,
        summary=_bounded_summary(item),
    ).digest
    stock_digest = observation_from_summary(
        run_id=RUN_ID,
        step=2,
        tool_name="stock.projected",
        ok=True,
        summary=_bounded_summary(stock),
    ).digest
    provider = _ClosableProvider(
        [
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call-1",
                        name="item.lookup",
                        arguments='{"query":"ITEM-1"}',
                    ),
                ),
                prompt_tokens=10,
                completion_tokens=3,
            ),
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call-2",
                        name="stock.projected",
                        arguments='{"item_code":"ITEM-1"}',
                    ),
                ),
                prompt_tokens=12,
                completion_tokens=4,
            ),
            ProviderResponse(
                text=json.dumps(
                    {
                        "status": "SUCCEEDED",
                        "summary": "facts collected",
                        "evidence_refs": [stock_digest],
                    }
                ),
                prompt_tokens=14,
                completion_tokens=5,
            ),
        ]
    )
    gateway = _FakeGatewayClient({"item.lookup": item, "stock.projected": stock})
    pricing = Pricing(
        input_microusd_per_million=Decimal("0"),
        output_microusd_per_million=Decimal("0"),
        reasoning_microusd_per_million=Decimal("0"),
    )
    monkeypatch.setattr("agent_runtime.agent.execution.pricing_from_environment", lambda: pricing)
    monkeypatch.setattr("agent_runtime.agent.execution.provider_from_environment", lambda: provider)
    monkeypatch.setattr("agent_runtime.agent.execution.GatewayClient", lambda: gateway)

    response = asyncio.run(execute_agent(AgentExecuteRequest.model_validate(_request_payload())))

    assert response.result.stop_reason.code == "FINAL_ANSWER"
    assert [request.tool.name for request in gateway.requests] == [
        "item.lookup",
        "stock.projected",
    ]
    assert response.result.final_answer is not None
    assert response.result.final_answer.evidence_refs == (stock_digest,)
    assert item_digest != stock_digest
    assert provider.closed is True
    assert gateway.closed is True
    assert CAPABILITY not in repr(response)


def test_execute_agent_closes_gateway_when_provider_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _FakeGatewayClient({})
    pricing = Pricing(
        input_microusd_per_million=Decimal("0"),
        output_microusd_per_million=Decimal("0"),
        reasoning_microusd_per_million=Decimal("0"),
    )

    monkeypatch.setattr("agent_runtime.agent.execution.pricing_from_environment", lambda: pricing)

    def fail_provider_setup() -> object:
        raise RuntimeError("provider configuration is invalid")

    monkeypatch.setattr(
        "agent_runtime.agent.execution.provider_from_environment", fail_provider_setup
    )
    monkeypatch.setattr("agent_runtime.agent.execution.GatewayClient", lambda: gateway)

    response = asyncio.run(execute_agent(AgentExecuteRequest.model_validate(_request_payload())))

    assert response.result.stop_reason.code == "MODEL_ERROR"
    assert gateway.closed is True


async def _post_agent(
    payload: Mapping[str, object], headers: Mapping[str, str] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.post("/agent/execute", json=payload, headers=headers)


def test_agent_endpoint_requires_internal_auth_and_fails_closed_without_pricing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNORA_RUNTIME_TOKEN", raising=False)
    response = asyncio.run(_post_agent(_request_payload()))
    assert response.status_code == 503

    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", "runtime-secret")
    response = asyncio.run(_post_agent(_request_payload(), {"X-Synora-Runtime-Token": "wrong"}))
    assert response.status_code == 401

    monkeypatch.delenv("SYNORA_PRICE_INPUT_MICROUSD_PER_MILLION", raising=False)
    monkeypatch.delenv("SYNORA_PRICE_OUTPUT_MICROUSD_PER_MILLION", raising=False)
    monkeypatch.delenv("SYNORA_PRICE_REASONING_MICROUSD_PER_MILLION", raising=False)
    response = asyncio.run(
        _post_agent(_request_payload(), {"X-Synora-Runtime-Token": "runtime-secret"})
    )
    assert response.status_code == 200
    assert response.json()["result"]["stop_reason"]["code"] == "COST_BUDGET"
