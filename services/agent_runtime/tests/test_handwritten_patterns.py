"""P4.2 offline tests for the handwritten pattern comparison set."""

import asyncio
from uuid import NAMESPACE_URL, UUID, uuid5

from agent_runtime.agent.contracts import Observation, ToolName, observation_from_summary
from agent_runtime.evaluation.loader import AgentEvaluationCase, load_agent_cases

from labs.agent_patterns.comparison import compare_case, summarize_run
from labs.agent_patterns.handwritten import (
    DirectRunner,
    MiniStepAgent,
    PlanAndSolveRunner,
    ReActRunner,
    ReflectionRunner,
)
from labs.agent_patterns.react_lab import RecordedToolAdapter, ScriptedModel

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")


def _case(case_id: str = "P4-G01-observation-driven-second-tool") -> AgentEvaluationCase:
    return next(case for case in load_agent_cases().cases if case.case_id == case_id)


def _correlation_id(case: AgentEvaluationCase) -> UUID:
    return uuid5(NAMESPACE_URL, f"phase4-lab:{case.case_id}:correlation")


def _action(
    case: AgentEvaluationCase,
    tool_name: str,
    args: dict[str, object],
    step: int,
) -> dict[str, object]:
    return {
        "type": "action",
        "step": step,
        "tool_name": tool_name,
        "canonical_args": args,
        "correlation_id": str(_correlation_id(case)),
    }


def _observation(tool_name: ToolName, summary: str) -> Observation:
    return observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name=tool_name,
        ok=True,
        summary=summary,
    )


def _g01_adapter() -> RecordedToolAdapter:
    return RecordedToolAdapter(
        {
            "material_request.open": _observation(
                "material_request.open", "open material request for SYNORA-P1-Item-1001"
            ),
            "stock.projected": _observation("stock.projected", "projected stock is 60"),
        }
    )


def test_direct_runner_returns_one_final_without_tool_calls() -> None:
    case = _case()
    provider = ScriptedModel([{"type": "final", "status": "SUCCEEDED", "summary": "direct draft"}])

    result = asyncio.run(DirectRunner().run(case, provider, _g01_adapter()))

    assert result.stop_reason.code == "FINAL_ANSWER"
    assert result.final_answer is not None
    assert provider.calls == 1


def test_react_runner_uses_observation_to_select_second_tool() -> None:
    case = _case()
    first = _observation("material_request.open", "open material request")
    second = _observation("stock.projected", "projected stock is 60")
    provider = ScriptedModel(
        [
            _action(case, "material_request.open", {}, 1),
            _action(case, "stock.projected", {"item_code": "SYNORA-P1-Item-1001"}, 2),
            {
                "type": "final",
                "status": "SUCCEEDED",
                "summary": "read-only facts collected",
                "evidence_refs": [second.digest],
            },
        ]
    )
    adapter = RecordedToolAdapter({"material_request.open": first, "stock.projected": second})

    result = asyncio.run(ReActRunner().run(case, provider, adapter))

    assert result.stop_reason.code == "FINAL_ANSWER"
    assert [action.tool_name for action in adapter.calls] == [
        "material_request.open",
        "stock.projected",
    ]


def test_plan_and_solve_executes_a_typed_short_plan() -> None:
    case = _case()
    provider = ScriptedModel(
        [
            {
                "type": "plan",
                "summary": "read two facts",
                "final_summary": "facts collected",
                "steps": [
                    {"type": "action", "step": 1, "tool_name": "material_request.open"},
                    {
                        "type": "action",
                        "step": 2,
                        "tool_name": "stock.projected",
                        "canonical_args": {"item_code": "SYNORA-P1-Item-1001"},
                    },
                ],
            }
        ]
    )
    adapter = _g01_adapter()

    result = asyncio.run(PlanAndSolveRunner().run(case, provider, adapter))

    assert result.stop_reason.code == "FINAL_ANSWER"
    assert [action.tool_name for action in adapter.calls] == [
        "material_request.open",
        "stock.projected",
    ]


def test_reflection_runner_stops_after_one_critic_pass() -> None:
    case = _case()
    provider = ScriptedModel(
        [
            {"type": "final", "status": "SUCCEEDED", "summary": "draft"},
            {"type": "critic", "accepted": True},
        ]
    )

    result = asyncio.run(ReflectionRunner().run(case, provider, _g01_adapter()))

    assert result.stop_reason.code == "FINAL_ANSWER"
    assert provider.calls == 2


def test_mini_step_agent_reuses_bounded_kernel_defaults() -> None:
    case = _case()
    second = _observation("stock.projected", "projected stock is 60")
    provider = ScriptedModel(
        [
            _action(case, "material_request.open", {}, 1),
            _action(case, "stock.projected", {"item_code": "SYNORA-P1-Item-1001"}, 2),
            {
                "type": "final",
                "status": "SUCCEEDED",
                "summary": "facts collected",
                "evidence_refs": [second.digest],
            },
        ]
    )
    adapter = _g01_adapter()

    result = asyncio.run(MiniStepAgent().run(case, provider, adapter))

    assert result.stop_reason.code == "FINAL_ANSWER"
    assert len(adapter.calls) == 2


def test_comparison_records_preserve_pattern_order_and_metrics() -> None:
    case = _case()
    direct = asyncio.run(
        DirectRunner().run(
            case,
            ScriptedModel([{"type": "final", "status": "SUCCEEDED", "summary": "direct draft"}]),
            _g01_adapter(),
        )
    )
    react = asyncio.run(
        ReActRunner().run(
            case,
            ScriptedModel(
                [
                    _action(case, "material_request.open", {}, 1),
                    _action(
                        case,
                        "stock.projected",
                        {"item_code": "SYNORA-P1-Item-1001"},
                        2,
                    ),
                    {
                        "type": "final",
                        "status": "SUCCEEDED",
                        "summary": "facts collected",
                        "evidence_refs": [
                            _observation("stock.projected", "projected stock is 60").digest
                        ],
                    },
                ]
            ),
            _g01_adapter(),
        )
    )

    direct_metrics = summarize_run("direct", case, direct)
    comparison = compare_case(case, {"direct": direct, "react": react})

    assert direct_metrics.pattern == "direct"
    assert direct_metrics.tool_calls == 0
    assert [metric.pattern for metric in comparison] == ["direct", "react"]
    assert comparison[1].tool_calls == 2
    assert comparison[1].trajectory_correct
