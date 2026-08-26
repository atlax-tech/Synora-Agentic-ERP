"""P4.2 bounded ReAct kernel and recorded-adapter tests."""

import asyncio
from uuid import UUID

from agent_runtime.agent.contracts import Observation, ToolName, observation_from_summary
from agent_runtime.agent.kernel import KernelLimits, ToolExecutionFailure, run_bounded_react

from labs.agent_patterns.react_lab import (
    LearningRepeatedCallGuard,
    RecordedToolAdapter,
    ScriptedModel,
)

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")


def _observation(tool_name: ToolName, summary: str) -> Observation:
    return observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name=tool_name,
        ok=True,
        summary=summary,
    )


def _action(
    tool_name: str,
    args: dict[str, object] | None = None,
    step: int = 1,
) -> dict[str, object]:
    return {
        "type": "action",
        "step": step,
        "tool_name": tool_name,
        "canonical_args": args or {"query": "bearing"},
        "correlation_id": str(CORRELATION_ID),
    }


class ReferenceRepeatedCallGuard:
    """Test oracle for the kernel; Assignment 2 re-implements this in the lab."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def check(self, action):  # type: ignore[no-untyped-def]
        key = action.call_key()
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


def test_observation_drives_second_different_tool_and_final_answer() -> None:
    first = _observation("item.lookup", "SYNORA-P1-Item-1001")
    second = _observation("stock.projected", "projected stock is 60")
    model = ScriptedModel(
        [
            _action("item.lookup", {"query": "SYNORA-P1-Item-1001"}, step=1),
            _action("stock.projected", {"item_code": "SYNORA-P1-Item-1001"}, step=2),
            {
                "type": "final",
                "status": "SUCCEEDED",
                "summary": "observations collected",
                "evidence_refs": [second.digest],
            },
        ]
    )
    adapter = RecordedToolAdapter({"item.lookup": first, "stock.projected": second})
    result = asyncio.run(
        run_bounded_react(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            model=model,
            tool_adapter=adapter,
            allowed_tools=frozenset({"item.lookup", "stock.projected"}),
            repeat_guard=ReferenceRepeatedCallGuard(),
        )
    )
    assert result.stop_reason.code == "FINAL_ANSWER"
    assert [action.tool_name for action in adapter.calls] == ["item.lookup", "stock.projected"]
    assert model.calls == 3


def test_unknown_tool_is_rejected_before_adapter_execution() -> None:
    model = ScriptedModel([_action("purchase.submit", step=1)])
    adapter = RecordedToolAdapter({"item.lookup": _observation("item.lookup", "unused")})
    result = asyncio.run(
        run_bounded_react(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            model=model,
            tool_adapter=adapter,
            allowed_tools=frozenset({"item.lookup"}),
            repeat_guard=ReferenceRepeatedCallGuard(),
        )
    )
    assert result.stop_reason.code == "TOOL_NOT_ALLOWED"
    assert adapter.calls == []


def test_invalid_arguments_are_rejected_before_adapter_execution() -> None:
    model = ScriptedModel([_action("item.lookup", {"unexpected": True}, step=1)])
    adapter = RecordedToolAdapter({"item.lookup": _observation("item.lookup", "unused")})
    result = asyncio.run(
        run_bounded_react(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            model=model,
            tool_adapter=adapter,
            allowed_tools=frozenset({"item.lookup"}),
            repeat_guard=ReferenceRepeatedCallGuard(),
        )
    )
    assert result.stop_reason.code == "INVALID_TOOL_ARGS"
    assert adapter.calls == []


def test_tool_failure_is_classified_and_stops() -> None:
    model = ScriptedModel([_action("purchase_order.open", {"limit": 20}, step=1)])
    adapter = RecordedToolAdapter(
        {"purchase_order.open": ToolExecutionFailure("TOOL_TIMEOUT", retryable=True)}
    )
    result = asyncio.run(
        run_bounded_react(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            model=model,
            tool_adapter=adapter,
            allowed_tools=frozenset({"purchase_order.open"}),
            repeat_guard=ReferenceRepeatedCallGuard(),
        )
    )
    assert result.stop_reason.code == "TOOL_ERROR"
    assert len(adapter.calls) == 1
    assert any(event.event_type == "tool.failed" for event in result.events)


def test_observation_context_mismatch_stops_before_next_model_call() -> None:
    class MismatchedObservationAdapter:
        async def execute(self, action):  # type: ignore[no-untyped-def]
            return _observation("item.lookup", "wrong context")

    model = ScriptedModel([_action("stock.projected", {"item_code": "SYNORA-P1-Item-1001"})])
    result = asyncio.run(
        run_bounded_react(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            model=model,
            tool_adapter=MismatchedObservationAdapter(),
            allowed_tools=frozenset({"stock.projected"}),
            repeat_guard=ReferenceRepeatedCallGuard(),
        )
    )

    assert result.stop_reason.code == "TOOL_ERROR"
    assert model.calls == 1


def test_reference_repeat_guard_stops_before_second_tool_execution() -> None:
    observation = _observation("stock.projected", "projected stock is 60")
    model = ScriptedModel(
        [
            _action("stock.projected", {"item_code": "SYNORA-P1-Item-1001"}, step=1),
            _action("stock.projected", {"item_code": "SYNORA-P1-Item-1001"}, step=2),
        ]
    )
    adapter = RecordedToolAdapter({"stock.projected": observation})
    result = asyncio.run(
        run_bounded_react(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            model=model,
            tool_adapter=adapter,
            allowed_tools=frozenset({"stock.projected"}),
            repeat_guard=ReferenceRepeatedCallGuard(),
        )
    )
    assert result.stop_reason.code == "REPEATED_CALL"
    assert len(adapter.calls) == 1
    assert model.calls == 2


def test_learning_guard_stops_before_second_tool_execution() -> None:
    observation = _observation("stock.projected", "projected stock is 60")
    model = ScriptedModel(
        [
            _action("stock.projected", {"item_code": "SYNORA-P1-Item-1001"}, step=1),
            _action("stock.projected", {"item_code": "SYNORA-P1-Item-1001"}, step=2),
        ]
    )
    adapter = RecordedToolAdapter({"stock.projected": observation})
    result = asyncio.run(
        run_bounded_react(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            model=model,
            tool_adapter=adapter,
            allowed_tools=frozenset({"stock.projected"}),
            repeat_guard=LearningRepeatedCallGuard(),
            limits=KernelLimits(max_steps=2),
        )
    )
    assert result.stop_reason.code == "REPEATED_CALL"
