"""Runtime workflow HTTP contract and resume/cancel behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from agent_runtime.agent.contracts import Observation, observation_from_summary
from agent_runtime.app import app

CAPABILITY = "A" * 43
TOKEN = "runtime-secret"


def _payload(run_id: UUID, *, steps: list[dict[str, object]] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "run_id": str(run_id),
        "correlation_id": str(uuid4()),
        "goal": "clarify warehouse before reading",
        "capability": CAPABILITY,
    }
    if steps is not None:
        value["steps"] = steps
    return value


async def _post(
    path: str,
    payload: Mapping[str, object],
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.post(path, json=payload, headers=headers)


def _clarification_steps() -> list[dict[str, object]]:
    return [
        {
            "step_id": "ask-warehouse",
            "order": 1,
            "type": "CLARIFICATION",
            "clarification": {
                "interrupt_id": str(uuid4()),
                "question": "Which warehouse?",
                "answer_type": "TEXT",
                "answer_max_length": 40,
            },
        },
        {
            "step_id": "read-mr",
            "order": 2,
            "type": "TOOL",
            "depends_on": ["ask-warehouse"],
            "allowed_tools": ["material_request.open"],
            "tool_name": "material_request.open",
            "parameters": {"offset": 0, "limit": 20},
        },
    ]


class _FakeClient:
    async def aclose(self) -> None:
        return None


class _FakeAdapter:
    def __init__(self, **kwargs: object) -> None:
        del kwargs

    async def execute(self, action: object) -> Observation:
        return observation_from_summary(
            run_id=uuid4(),
            step=1,
            tool_name="material_request.open",
            ok=True,
            summary="recorded read",
        )

    async def aclose(self) -> None:
        return None


def test_workflow_routes_require_static_runtime_token_and_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNORA_RUNTIME_TOKEN", raising=False)
    monkeypatch.setenv("SYNORA_WORKFLOW_DB_PATH", "/tmp/synora-workflow-api-test.sqlite")
    response = asyncio.run(_post("/workflow/start", _payload(uuid4())))
    assert response.status_code == 503
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", TOKEN)
    response = asyncio.run(_post("/workflow/start", _payload(uuid4())))
    assert response.status_code == 401


def test_workflow_start_interrupts_resume_is_revision_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setenv("SYNORA_WORKFLOW_DB_PATH", str(tmp_path / "workflow.sqlite"))
    monkeypatch.setattr("agent_runtime.workflow.runtime.GatewayClient", _FakeClient)
    monkeypatch.setattr("agent_runtime.workflow.runtime.GatewayToolAdapter", _FakeAdapter)
    run_id = uuid4()

    started = asyncio.run(
        _post(
            "/workflow/start",
            _payload(run_id, steps=_clarification_steps()),
            {"X-Synora-Runtime-Token": TOKEN},
        )
    )
    assert started.status_code == 200, started.text
    state = started.json()["result"]["state"]
    assert state["status"] == "INTERRUPTED"
    assert state["clarification"]["interrupt_id"]

    resume_payload = {
        "run_id": str(run_id),
        "correlation_id": str(uuid4()),
        "workflow_revision": state["revision"],
        "interrupt_id": state["clarification"]["interrupt_id"],
        "answer": "Stores",
        "capability": CAPABILITY,
    }
    resumed = asyncio.run(
        _post("/workflow/resume", resume_payload, {"X-Synora-Runtime-Token": TOKEN})
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["result"]["state"]["status"] == "SUCCEEDED"

    stale = asyncio.run(
        _post("/workflow/resume", resume_payload, {"X-Synora-Runtime-Token": TOKEN})
    )
    assert stale.status_code == 409


def test_workflow_cancel_is_terminal_and_status_is_readable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SYNORA_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setenv("SYNORA_WORKFLOW_DB_PATH", str(tmp_path / "workflow.sqlite"))
    monkeypatch.setattr("agent_runtime.workflow.runtime.GatewayClient", _FakeClient)
    monkeypatch.setattr("agent_runtime.workflow.runtime.GatewayToolAdapter", _FakeAdapter)
    run_id = uuid4()
    started = asyncio.run(
        _post(
            "/workflow/start",
            _payload(run_id, steps=_clarification_steps()),
            {"X-Synora-Runtime-Token": TOKEN},
        )
    )
    revision = started.json()["result"]["state"]["revision"]
    cancelled = asyncio.run(
        _post(
            "/workflow/cancel",
            {"run_id": str(run_id), "workflow_revision": revision},
            {"X-Synora-Runtime-Token": TOKEN},
        )
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["result"]["state"]["status"] == "CANCELLED"
    status = asyncio.run(
        _post(
            "/workflow/status",
            {"run_id": str(run_id)},
            {"X-Synora-Runtime-Token": TOKEN},
        )
    )
    assert status.status_code == 200
    assert status.json()["result"]["state"]["status"] == "CANCELLED"
