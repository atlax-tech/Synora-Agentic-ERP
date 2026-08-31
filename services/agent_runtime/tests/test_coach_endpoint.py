"""T08.1 internal Coach HTTP boundary tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
from agent_runtime.app import _coach_with_disconnect_guard, app
from agent_runtime.coach.contracts import CoachAnswer, CoachRetrievalTrace
from agent_runtime.coach.runtime import CoachRuntimeRequest
from fastapi import HTTPException

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")
CAPABILITY = "A" * 43


def _payload(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1",
        "run_id": str(RUN_ID),
        "correlation_id": str(CORRELATION_ID),
        "question": "What quantity remains open?",
        "current_document": {"doctype": "Material Request", "name": "MAT-MR-0001"},
        "capability": CAPABILITY,
    }
    payload.update(extra)
    return payload


async def _post_coach(
    payload: Mapping[str, object], headers: Mapping[str, str] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.post("/coach/answer", json=payload, headers=headers)


def _safe_answer() -> CoachAnswer:
    return CoachAnswer(
        schema_version="1",
        answer_status="UNKNOWN",
        answer="",
        claims=(),
        citations=(),
        refusal_reason="current ERP context is not available",
        retrieval_trace=CoachRetrievalTrace(),
        latency_ms=0,
    )


def test_coach_endpoint_requires_internal_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNORA_RUNTIME_TOKEN", raising=False)
    response = asyncio.run(_post_coach(_payload()))
    assert response.status_code == 503
    assert CAPABILITY not in response.text

    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", "runtime-secret")
    response = asyncio.run(_post_coach(_payload(), {"X-Synora-Runtime-Token": "wrong-token"}))
    assert response.status_code == 401
    assert CAPABILITY not in response.text


@pytest.mark.parametrize("field", ["facts", "retrieval_hits", "tools", "provider_config"])
def test_coach_endpoint_rejects_caller_authority_fields(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", "runtime-secret")
    response = asyncio.run(
        _post_coach(
            _payload(**{field: {"untrusted": True}}), {"X-Synora-Runtime-Token": "runtime-secret"}
        )
    )
    assert response.status_code == 422
    assert CAPABILITY not in response.text


def test_coach_endpoint_returns_strict_coach_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", "runtime-secret")
    seen: list[CoachRuntimeRequest] = []

    async def fake_runtime(request: CoachRuntimeRequest) -> CoachAnswer:
        seen.append(request)
        return _safe_answer()

    monkeypatch.setattr("agent_runtime.app.answer_coach_runtime", fake_runtime)
    response = asyncio.run(_post_coach(_payload(), {"X-Synora-Runtime-Token": "runtime-secret"}))

    assert response.status_code == 200
    assert response.json()["answer_status"] == "UNKNOWN"
    assert response.json()["claims"] == []
    assert len(seen) == 1
    assert seen[0].capability.get_secret_value() == CAPABILITY


class _DisconnectingRequest:
    def __init__(self) -> None:
        self.polls = 0

    async def is_disconnected(self) -> bool:
        self.polls += 1
        return self.polls > 1


def test_coach_disconnect_guard_cancels_inflight_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = False

    async def slow_runtime(_: CoachRuntimeRequest) -> CoachAnswer:
        nonlocal cancelled
        try:
            await asyncio.sleep(60)
        finally:
            cancelled = True
        return _safe_answer()

    monkeypatch.setattr("agent_runtime.app.answer_coach_runtime", slow_runtime)

    async def exercise() -> None:
        request = CoachRuntimeRequest.model_validate(_payload())
        with pytest.raises(HTTPException, match="request disconnected"):
            await _coach_with_disconnect_guard(request, _DisconnectingRequest())  # type: ignore[arg-type]

    asyncio.run(exercise())
    assert cancelled is True


def test_coach_endpoint_does_not_accept_arbitrary_response_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", "runtime-secret")

    async def invalid_runtime(_: CoachRuntimeRequest) -> Any:
        return {"answer_status": "ANSWERED", "answer": "not a CoachAnswer"}

    monkeypatch.setattr("agent_runtime.app.answer_coach_runtime", invalid_runtime)
    response = asyncio.run(_post_coach(_payload(), {"X-Synora-Runtime-Token": "runtime-secret"}))

    assert response.status_code == 500
    assert CAPABILITY not in response.text
