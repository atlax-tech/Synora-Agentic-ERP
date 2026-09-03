"""P9.7 A2A lifecycle tests through the official SDK client and ASGI loopback."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

pytest.importorskip("a2a")
from a2a.client.client import Client, ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.helpers import get_artifact_text, new_text_message
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetTaskRequest,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskState,
)
from fastapi import FastAPI

from labs.protocols.phase9_a2a import (
    DEFAULT_ENDPOINT,
    HANDLER_EXCEPTION_SENTINEL,
    MAX_STORED_TASKS,
    PolicyRiskReviewRequest,
    build_agent_card,
    build_app,
)


def _payload(*, unknowns: list[str] | None = None) -> dict[str, object]:
    return PolicyRiskReviewRequest(
        plan_digest="a" * 64,
        candidate_explanation="The typed procurement snapshot is sufficient for review.",
        unknowns=unknowns or [],
    ).model_dump(mode="json")


async def _client_for(app: FastAPI) -> tuple[httpx.AsyncClient, Client]:
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8029",
    )
    factory = ClientFactory(ClientConfig(streaming=False, httpx_client=http_client))
    client = await factory.create_from_url("http://127.0.0.1:8029")
    return http_client, client


def test_agent_card_is_loopback_a2a_10_and_has_one_skill() -> None:
    card = build_agent_card()
    assert card.version == "1.0.0"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
    assert card.supported_interfaces[0].url == DEFAULT_ENDPOINT
    assert len(card.skills) == 1
    assert card.skills[0].id == "policy-risk-review"
    assert card.capabilities.streaming is False


def test_typed_payload_rejects_extra_fields_and_control_text() -> None:
    with pytest.raises(ValueError):
        PolicyRiskReviewRequest.model_validate({**_payload(), "write": True})
    with pytest.raises(ValueError):
        PolicyRiskReviewRequest.model_validate(
            {**_payload(), "candidate_explanation": "bad\x00payload"}
        )
    with pytest.raises(ValueError):
        PolicyRiskReviewRequest.model_validate({**_payload(), "candidate_explanation": "x" * 4_001})
    with pytest.raises(ValueError):
        PolicyRiskReviewRequest.model_validate({**_payload(), "unknowns": ["x"] * 33})


def test_a2a_client_observes_completed_task_and_bound_artifact() -> None:
    asyncio.run(_test_a2a_client_observes_completed_task_and_bound_artifact())


async def _test_a2a_client_observes_completed_task_and_bound_artifact() -> None:
    app = build_app()
    http_client, client = await _client_for(app)
    try:
        request = SendMessageRequest(
            message=new_text_message(
                json.dumps(_payload()),
                media_type="application/json",
                role=Role.ROLE_USER,
            ),
            configuration=SendMessageConfiguration(return_immediately=False),
        )
        responses = [response async for response in client.send_message(request)]
        assert len(responses) == 1
        task = responses[0].task
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert task.id and task.context_id
        assert len(task.artifacts) == 1
        response = PolicyRiskReviewRequest.model_validate(_payload())
        artifact_text = get_artifact_text(task.artifacts[0])
        output = json.loads(artifact_text)
        assert output["task_id"] == task.id
        assert output["context_id"] == task.context_id
        assert output["reviewed_plan_digest"] == response.plan_digest
    finally:
        await client.close()
        await http_client.aclose()
        await app.state.phase9_handler.aclose()


def test_a2a_cancel_is_idempotent_and_does_not_complete() -> None:
    asyncio.run(_test_a2a_cancel_is_idempotent_and_does_not_complete())


async def _test_a2a_cancel_is_idempotent_and_does_not_complete() -> None:
    app = build_app()
    http_client, client = await _client_for(app)
    try:
        request = SendMessageRequest(
            message=new_text_message(
                json.dumps(_payload()),
                media_type="application/json",
                role=Role.ROLE_USER,
            ),
            configuration=SendMessageConfiguration(return_immediately=True),
        )
        responses = [response async for response in client.send_message(request)]
        task = responses[0].task
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED

        canceled = await client.cancel_task(CancelTaskRequest(id=task.id))
        canceled_again = await client.cancel_task(CancelTaskRequest(id=task.id))
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED
        assert canceled_again.status.state == TaskState.TASK_STATE_CANCELED
        current = await client.get_task(GetTaskRequest(id=task.id))
        assert current.status.state == TaskState.TASK_STATE_CANCELED
        assert not current.artifacts
    finally:
        await client.close()
        await http_client.aclose()
        await app.state.phase9_handler.aclose()


def test_a2a_rejects_task_context_mismatch() -> None:
    asyncio.run(_test_a2a_rejects_task_context_mismatch())


async def _test_a2a_rejects_task_context_mismatch() -> None:
    app = build_app(work_delay_seconds=0.2)
    http_client, client = await _client_for(app)
    try:
        request = SendMessageRequest(
            message=new_text_message(
                json.dumps(_payload()),
                media_type="application/json",
                role=Role.ROLE_USER,
            ),
            configuration=SendMessageConfiguration(return_immediately=True),
        )
        responses = [response async for response in client.send_message(request)]
        task = responses[0].task
        mismatched = SendMessageRequest(
            message=new_text_message(
                json.dumps(_payload()),
                media_type="application/json",
                context_id="wrong-context",
                task_id=task.id,
                role=Role.ROLE_USER,
            ),
            configuration=SendMessageConfiguration(return_immediately=False),
        )
        with pytest.raises(Exception, match="context"):
            _ = [response async for response in client.send_message(mismatched)]
        await client.cancel_task(CancelTaskRequest(id=task.id))
    finally:
        await client.close()
        await http_client.aclose()
        await app.state.phase9_handler.aclose()


def test_a2a_handler_exception_is_distinct_from_malformed_payload() -> None:
    asyncio.run(_test_a2a_handler_exception_is_distinct_from_malformed_payload())


async def _test_a2a_handler_exception_is_distinct_from_malformed_payload() -> None:
    app = build_app()
    http_client, client = await _client_for(app)
    try:
        request = SendMessageRequest(
            message=new_text_message(
                json.dumps(_payload_with_explanation(HANDLER_EXCEPTION_SENTINEL)),
                media_type="application/json",
                role=Role.ROLE_USER,
            ),
            configuration=SendMessageConfiguration(return_immediately=False),
        )
        with pytest.raises(Exception, match="handler exception probe"):
            _ = [response async for response in client.send_message(request)]
    finally:
        await client.close()
        await http_client.aclose()
        await app.state.phase9_handler.aclose()


def _payload_with_explanation(explanation: str) -> dict[str, object]:
    return PolicyRiskReviewRequest(
        plan_digest="a" * 64,
        candidate_explanation=explanation,
        unknowns=[],
    ).model_dump(mode="json")


def test_a2a_task_store_has_a_bounded_capacity() -> None:
    app = build_app()
    assert app.state.phase9_task_store._max_tasks == MAX_STORED_TASKS
