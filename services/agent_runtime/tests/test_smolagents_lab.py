"""Offline tests for the lab-only smolagents adapter."""

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest
from agent_runtime.agent.contracts import Observation, ToolName, observation_from_summary

from labs.agent_patterns.react_lab import RecordedToolAdapter
from labs.agent_patterns.smolagents_lab import (
    SMOLAGENTS_COMMIT,
    RecordedSmolagentsToolLedger,
    RecordedSmolagentsToolSet,
    SmolagentsToolError,
    run_smolagents_tool_calling,
)

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("bdf7dbe3-2e6e-4fa6-aa4d-3e8ec3cbf3cc")


def _observation(tool_name: ToolName, summary: str) -> Observation:
    return observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name=tool_name,
        ok=True,
        summary=summary,
    )


def _case() -> SimpleNamespace:
    return SimpleNamespace(
        goal="collect procurement facts",
        allowed_tools=("material_request.open", "stock.projected"),
    )


@dataclass
class _FakeResult:
    output: object
    state: str = "success"
    steps: list[dict[str, object]] | None = None
    token_usage: object | None = None
    timing: object | None = None


class _FakeAgent:
    def __init__(self, callback) -> None:  # type: ignore[no-untyped-def]
        self._callback = callback

    def run(
        self,
        task: str,
        *,
        reset: bool,
        max_steps: int,
        return_full_result: bool,
    ) -> object:
        assert task == "collect procurement facts"
        assert reset is True
        assert max_steps == 6
        assert return_full_result is True
        return self._callback()


def _ledger() -> RecordedSmolagentsToolLedger:
    return RecordedSmolagentsToolLedger(
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        tool_adapter=RecordedToolAdapter(
            {
                "material_request.open": _observation(
                    "material_request.open", "open material request"
                ),
                "stock.projected": _observation("stock.projected", "projected stock is 60"),
            }
        ),
        allowed_tools=frozenset({"material_request.open", "stock.projected"}),
    )


def test_adapter_translates_recorded_observation_driven_calls() -> None:
    ledger = _ledger()
    second = _observation("stock.projected", "projected stock is 60")

    def run_agent() -> _FakeResult:
        ledger.invoke("material_request.open", {})
        observed = ledger.invoke("stock.projected", {"item_code": "SYNORA-P1-Item-1001"})
        assert observed == second.summary
        return _FakeResult(
            output={
                "status": "SUCCEEDED",
                "summary": "facts collected",
                "evidence_refs": [ledger.calls[-1].observation.digest],  # type: ignore[union-attr]
            },
            steps=[
                {"tool_calls": [{"function": {"name": "material_request_open"}}]},
                {"tool_calls": [{"function": {"name": "stock_projected"}}]},
            ],
            token_usage=SimpleNamespace(input_tokens=20, output_tokens=12),
            timing=SimpleNamespace(start_time=10.0, end_time=10.125),
        )

    result = run_smolagents_tool_calling(
        case=_case(),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        agent=_FakeAgent(run_agent),
        recorded_tools=RecordedSmolagentsToolSet(tools=(), ledger=ledger),
    )

    assert result.stop_reason.code == "FINAL_ANSWER"
    assert result.final_answer is not None
    assert result.usage.prompt_tokens == 20
    assert result.usage.completion_tokens == 12
    assert [
        event.payload.get("tool_name")
        for event in result.events
        if event.event_type == "action.proposed"
    ] == ["material_request.open", "stock.projected"]


def test_adapter_stops_repeated_call_before_second_recorded_tool_execution() -> None:
    ledger = _ledger()

    def run_agent() -> _FakeResult:
        ledger.invoke("material_request.open", {})
        with pytest.raises(SmolagentsToolError, match="REPEATED_CALL"):
            ledger.invoke("material_request.open", {})
        return _FakeResult(
            output={"status": "FAILED", "summary": "stopped"},
            steps=[
                {"tool_calls": [{"function": {"name": "material_request_open"}}]},
                {"tool_calls": [{"function": {"name": "material_request_open"}}]},
            ],
        )

    result = run_smolagents_tool_calling(
        case=_case(),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        agent=_FakeAgent(run_agent),
        recorded_tools=RecordedSmolagentsToolSet(tools=(), ledger=ledger),
    )

    assert result.stop_reason.code == "REPEATED_CALL"
    assert len(ledger.calls) == 2
    assert ledger.calls[0].observation is not None
    assert ledger.calls[1].error_code == "REPEATED_CALL"


def test_adapter_rejects_parallel_and_unknown_smolagents_tool_calls() -> None:
    ledger = _ledger()

    parallel = run_smolagents_tool_calling(
        case=_case(),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        agent=_FakeAgent(
            lambda: _FakeResult(
                output={"status": "FAILED", "summary": "parallel"},
                steps=[
                    {
                        "tool_calls": [
                            {"function": {"name": "material_request_open"}},
                            {"function": {"name": "stock_projected"}},
                        ]
                    }
                ],
            )
        ),
        recorded_tools=RecordedSmolagentsToolSet(tools=(), ledger=ledger),
    )
    assert parallel.stop_reason.code == "MODEL_ERROR"

    unknown = run_smolagents_tool_calling(
        case=_case(),
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        agent=_FakeAgent(
            lambda: _FakeResult(
                output={"status": "FAILED", "summary": "unknown"},
                steps=[{"tool_calls": [{"function": {"name": "purchase_submit"}}]}],
            )
        ),
        recorded_tools=RecordedSmolagentsToolSet(tools=(), ledger=_ledger()),
    )
    assert unknown.stop_reason.code == "TOOL_NOT_ALLOWED"


def test_snapshot_identity_is_explicit_and_lab_only() -> None:
    assert SMOLAGENTS_COMMIT.startswith("30bb1161095d")
