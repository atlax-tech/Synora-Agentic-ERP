"""P9.3 bounded Planner -> Reviewer workflow tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from agent_runtime.multi_agent.contracts import (
    MultiAgentLimits,
    OrchestrationScope,
    plan_view_digest,
    plan_view_from_mapping,
)
from agent_runtime.multi_agent.planner_reviewer import run_planner_reviewer
from agent_runtime.providers import (
    ProviderMessage,
    ProviderResponse,
    ProviderResponseFormat,
    ProviderToolCall,
    ProviderToolSpec,
)

PLAN: dict[str, object] = {
    "goal": "ensure stock for ITEM-9",
    "horizon_days": 90,
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
    "generated_at": "2026-09-03T00:00:00+08:00",
}


def _digest() -> str:
    return plan_view_digest(plan_view_from_mapping(PLAN))


def _planner(text: str = "该物料缺口 8.0，建议补货 ITEM-9。", *, digest: str | None = None) -> str:
    return json.dumps(
        {
            "candidate_explanation": text,
            "citation_summary": ["risk=SHORTAGE"],
            "unknowns": [],
            "plan_digest": digest or _digest(),
        },
        ensure_ascii=False,
    )


def _review(
    decision: str = "ACCEPT",
    *,
    issue_codes: list[str] | None = None,
    digest: str | None = None,
    feedback: str = "",
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "issue_codes": issue_codes or [],
            "feedback": feedback,
            "reviewed_plan_digest": digest or _digest(),
        },
        ensure_ascii=False,
    )


class RecordingProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[ProviderMessage], list[ProviderToolSpec]]] = []

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        del model, max_tokens, response_format
        self.calls.append((messages, tools or []))
        return self.responses.pop(0)


def _response(text: str, *, prompt: int = 2, completion: int = 3) -> ProviderResponse:
    return ProviderResponse(text=text, prompt_tokens=prompt, completion_tokens=completion)


def test_accept_is_deterministically_validated_and_tools_are_empty() -> None:
    provider = RecordingProvider([_response(_planner()), _response(_review())])
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "ACCEPTED"
    assert result.deterministic_validated is True
    assert result.handoff_count == 1
    assert result.stop_reason.model_calls == 2
    assert all(tools == [] for _, tools in provider.calls)


def test_role_prompt_drops_capability_and_cookie_from_untrusted_plan() -> None:
    provider = RecordingProvider([_response(_planner()), _response(_review())])
    plan = {**PLAN, "requested_capability": "purchase.submit", "cookie": "secret-cookie"}
    result = asyncio.run(run_planner_reviewer(plan, provider))
    assert result.stop_reason.code == "ACCEPTED"
    prompt_text = "\n".join(message.content for message in provider.calls[0][0])
    assert "purchase.submit" not in prompt_text
    assert "secret-cookie" not in prompt_text


def test_role_visible_fields_bound_each_prompt_projection() -> None:
    provider = RecordingProvider([_response(_planner()), _response(_review())])
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "ACCEPTED"
    planner_text = "\n".join(message.content for message in provider.calls[0][0])
    reviewer_text = "\n".join(message.content for message in provider.calls[1][0])
    assert "generated_at" not in planner_text
    assert "Test Company" in planner_text
    assert "Test Company" not in reviewer_text
    assert "horizon_days" not in reviewer_text
    assert "warehouse" not in reviewer_text


def test_reviewer_accept_cannot_override_deterministic_validation() -> None:
    provider = RecordingProvider(
        [
            _response(_planner("库存 2.0 充足，无需采购。")),
            _response(_review()),
        ]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "DETERMINISTIC_FALLBACK"
    assert result.final_text == str(PLAN["summary"])
    assert result.deterministic_validated is False
    assert result.reviewer_decision is not None
    assert result.reviewer_decision.decision == "ACCEPT"


def test_chinese_erp_action_candidate_is_rejected_before_review() -> None:
    provider = RecordingProvider(
        [
            _response(_planner("库存 2.0，建议提交采购订单。")),
        ]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert result.stop_reason.model_calls == 1


@pytest.mark.parametrize(
    "text",
    [
        "库存 2.0，建议提交订单。",
        "库存 2.0，建议创建订单。",
        "库存 2.0，建议自动下单。",
        "库存 2.0，建议批准采购。",
        "库存 2.0，建议取消订单。",
        "库存 2.0，建议运行工具创建订单。",
        "库存 2.0，建议发起采购订单。",
        "库存 2.0，建议生成采购订单。",
        "库存 2.0，建议建立采购订单。",
        "库存 2.0，建议发送采购订单。",
        "库存 2.0，建议授权采购订单。",
        "库存 2.0，建议录入采购订单。",
        "库存 2.0，建议foobar采购订单。",
        "库存 2.0，建议foobar PO。",
        "库存 2.0，建议提交 PO。",
        "库存 2.0，建议创建 MR。",
        "库存 2.0，建议 submit the PO。",
        "库存 2.0，建议 send the purchase order。",
        "库存 2.0，建议 confirm the order。",
        "库存 2.0，建议 release the order。",
        "库存 2.0，建议 issue the purchase order。",
        "库存 2.0，建议提交 订单。",
        "库存 2.0，建议提交\n订单。",
        "库存 2.0，建议提\u200b交订单。",
        "库存 2.0，建议提-交订单。",
        "库存 2.0，建议提/交订单。",
        "库存 2.0，对订单进行提交。",
        "库存 2.0，将采购订单提交。",
        "库存 2.0，订单提交。",
        "库存 2.0，建议ｓｕｂｍｉｔ ｏｒｄｅｒ。",
    ],
)
def test_affirmative_erp_action_variants_are_rejected_before_review(text: str) -> None:
    provider = RecordingProvider([_response(_planner(text))])
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert result.stop_reason.model_calls == 1


def test_one_revision_is_bounded_to_three_calls() -> None:
    provider = RecordingProvider(
        [
            _response(_planner("库存 2.0，建议补货 ITEM-9。")),
            _response(_review("REVISE", issue_codes=["UNSUPPORTED_CLAIM"], feedback="请保留引用")),
            _response(_planner()),
        ]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "REVISED_ACCEPTED"
    assert result.stop_reason.model_calls == 3
    assert result.revision_count == 1
    assert result.handoff_count == 2
    assert len(provider.calls) == 3


def test_second_revision_failure_falls_back_without_a_fourth_call() -> None:
    provider = RecordingProvider(
        [
            _response(_planner()),
            _response(_review("REVISE", issue_codes=["UNSUPPORTED_CLAIM"])),
            _response(_planner("缺口 999，建议补货 ITEM-9。")),
        ]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "DETERMINISTIC_FALLBACK"
    assert result.stop_reason.model_calls == 3
    assert len(provider.calls) == 3


@pytest.mark.parametrize(
    ("decision", "code"),
    [("REJECT", "REVIEW_REJECTED"), ("ESCALATE", "REVIEW_ESCALATED")],
)
def test_reject_and_escalate_never_show_candidate(decision: str, code: str) -> None:
    provider = RecordingProvider(
        [_response(_planner()), _response(_review(decision, issue_codes=["RISK_CONFLICT"]))]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == code
    assert result.final_text == str(PLAN["summary"])
    assert result.deterministic_validated is False


@pytest.mark.parametrize("bad_text", ["not-json", json.dumps({"candidate_explanation": "x"})])
def test_invalid_planner_output_fails_closed(bad_text: str) -> None:
    provider = RecordingProvider([_response(bad_text)])
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert result.stop_reason.model_calls == 1


def test_invalid_reviewer_output_fails_closed() -> None:
    provider = RecordingProvider([_response(_planner()), _response("{}")])
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert result.stop_reason.model_calls == 2


def test_scalar_tuple_fields_are_normalized_without_relaxing_schema() -> None:
    planner = json.loads(_planner())
    planner["citation_summary"] = "risk=SHORTAGE"
    planner["unknowns"] = "none"
    provider = RecordingProvider(
        [
            _response(json.dumps(planner, ensure_ascii=False)),
            _response(_review()),
        ]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "ACCEPTED"
    assert result.deterministic_validated is True


def test_digest_mismatch_is_rejected_before_handoff() -> None:
    provider = RecordingProvider([_response(_planner(digest="f" * 64))])
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "DIGEST_MISMATCH"
    assert result.handoff_count == 0


def test_scope_mismatch_fails_before_any_model_call() -> None:
    provider = RecordingProvider([])
    scope = OrchestrationScope(
        task_id=UUID("00000000-0000-0000-0000-000000000001"),
        run_id=UUID("00000000-0000-0000-0000-000000000002"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000003"),
        principal="buyer@example.test",
        company="Other Company",
        warehouse="Main",
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider, scope=scope))
    assert result.stop_reason.code == "SCOPE_MISMATCH"
    assert result.stop_reason.model_calls == 0
    assert result.final_text == "无法生成计划解释，请人工核对确定性计划。"
    assert provider.calls == []


def test_failover_provider_is_rejected_to_keep_physical_calls_bounded() -> None:
    from agent_runtime.providers import FailoverProvider

    provider = FailoverProvider(RecordingProvider([]), RecordingProvider([]))
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "MODEL_ERROR"
    assert result.stop_reason.model_calls == 0


def test_invalid_projection_uses_constant_safe_fallback() -> None:
    provider = RecordingProvider([])
    result = asyncio.run(
        run_planner_reviewer(
            {"summary": "purchase.submit secret: abc", "findings": []},
            provider,
        )
    )
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert result.final_text == "无法生成计划解释，请人工核对确定性计划。"
    assert result.stop_reason.model_calls == 0


def test_oversized_projection_fails_closed() -> None:
    provider = RecordingProvider([])
    result = asyncio.run(
        run_planner_reviewer(
            {
                **PLAN,
                "findings": [
                    {
                        **PLAN["findings"][0],
                        "evidence": ["x" * 501],
                    }
                ],
            },
            provider,
        )
    )
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert provider.calls == []


def test_missing_or_over_budget_usage_fails_closed() -> None:
    missing = RecordingProvider([_response(_planner(), prompt=0, completion=0)])
    missing_result = asyncio.run(run_planner_reviewer(PLAN, missing))
    assert missing_result.stop_reason.code == "INVALID_OUTPUT"
    over = RecordingProvider([_response(_planner(), completion=257)])
    over_result = asyncio.run(run_planner_reviewer(PLAN, over))
    assert over_result.stop_reason.code == "BUDGET_EXCEEDED"


@pytest.mark.parametrize("prompt,completion", [(-1, 2), (2, -1)])
def test_negative_usage_fails_closed_without_contract_validation_error(
    prompt: int, completion: int
) -> None:
    provider = RecordingProvider([_response(_planner(), prompt=prompt, completion=completion)])
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert result.stop_reason.model_calls == 1
    assert result.role_usage[0].calls == 1


def test_max_depth_blocks_revision_before_second_handoff() -> None:
    provider = RecordingProvider(
        [
            _response(_planner()),
            _response(_review("REVISE", issue_codes=["UNSUPPORTED_CLAIM"])),
        ]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider, limits=MultiAgentLimits(max_depth=1)))
    assert result.stop_reason.code == "LOOP_BLOCKED"
    assert result.stop_reason.model_calls == 2
    assert result.handoff_count == 1
    assert result.revision_count == 0
    assert len(provider.calls) == 2


def test_provider_tool_calls_are_rejected_even_when_text_is_valid() -> None:
    provider = RecordingProvider(
        [
            ProviderResponse(
                text=_planner(),
                tool_calls=(ProviderToolCall(id="1", name="gateway", arguments="{}"),),
                prompt_tokens=1,
                completion_tokens=1,
            )
        ]
    )
    result = asyncio.run(run_planner_reviewer(PLAN, provider))
    assert result.stop_reason.code == "INVALID_OUTPUT"
    assert result.stop_reason.model_calls == 1


def test_wall_time_budget_cancels_slow_provider() -> None:
    class SlowProvider(RecordingProvider):
        async def complete(
            self,
            messages: list[ProviderMessage],
            tools: list[ProviderToolSpec] | None = None,
            model: str | None = None,
            max_tokens: int | None = None,
            response_format: ProviderResponseFormat | None = None,
        ) -> ProviderResponse:
            await asyncio.sleep(1.05)
            return await super().complete(messages, tools, model, max_tokens, response_format)

    provider = SlowProvider([_response(_planner())])
    result = asyncio.run(
        run_planner_reviewer(PLAN, provider, limits=MultiAgentLimits(max_wall_time_seconds=1))
    )
    assert result.stop_reason.code == "TIMEOUT"
    assert result.stop_reason.model_calls == 1


def test_cancellation_event_wins_race_and_discards_late_result() -> None:
    class SlowProvider(RecordingProvider):
        async def complete(
            self,
            messages: list[ProviderMessage],
            tools: list[ProviderToolSpec] | None = None,
            model: str | None = None,
            max_tokens: int | None = None,
            response_format: ProviderResponseFormat | None = None,
        ) -> ProviderResponse:
            await asyncio.sleep(0.05)
            return await super().complete(messages, tools, model, max_tokens, response_format)

    async def run() -> Any:
        event = asyncio.Event()
        provider = SlowProvider([_response(_planner())])
        task = asyncio.create_task(run_planner_reviewer(PLAN, provider, cancellation_event=event))
        await asyncio.sleep(0.005)
        event.set()
        return await task

    result = asyncio.run(run())
    assert result.stop_reason.code == "CANCELLED"
    assert result.stop_reason.model_calls == 1
    assert result.handoff_count == 0
