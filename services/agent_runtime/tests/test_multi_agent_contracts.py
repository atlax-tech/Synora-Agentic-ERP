"""P9.3 strict contract and boundary tests."""

from uuid import UUID

import pytest
from agent_runtime.multi_agent.contracts import (
    HandoffEnvelope,
    PlannerOutput,
    ReconciliationAdvice,
    ReviewDecision,
    RoleSpec,
    handoff_for,
    plan_view_digest,
    plan_view_from_mapping,
    validate_handoff_identity,
)
from pydantic import ValidationError

TASK_ID = UUID("00000000-0000-0000-0000-000000000001")
RUN_ID = UUID("00000000-0000-0000-0000-000000000002")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000003")


def test_role_specs_reject_unknown_visible_fields_and_reviewer_tools() -> None:
    with pytest.raises(ValidationError):
        RoleSpec(
            role_id="procurement_planner",
            version="1.0",
            visible_fields=("summary", "capability"),
            output_schema="planner.v1",
            call_budget=1,
        )
    with pytest.raises(ValidationError):
        RoleSpec(
            role_id="policy_risk_reviewer",
            version="1.0",
            visible_fields=("summary",),
            tool_allowlist=("stock.projected",),
            output_schema="review.v1",
            call_budget=1,
        )


def test_planner_review_and_reconciliation_contracts_have_no_action_escape() -> None:
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate(
            {
                "candidate_explanation": "只能提供只读分析",
                "citation_summary": (),
                "unknowns": (),
                "plan_digest": "0" * 64,
                "actions": (),
            }
        )
    with pytest.raises(ValidationError):
        ReviewDecision(
            decision="ACCEPT",
            issue_codes=("UNSAFE_ACTION",),
            reviewed_plan_digest="0" * 64,
        )
    with pytest.raises(ValidationError):
        ReconciliationAdvice(suggested_checks=("retry the ERP order",))
    with pytest.raises(ValidationError):
        ReconciliationAdvice(suggested_checks=("请重试并提交采购订单",))
    for action in (
        "建议提交订单",
        "建议创建订单",
        "建议自动下单",
        "建议批准采购",
        "建议取消订单",
        "建议提交 订单",
        "建议提交\n订单",
        "建议提\u200b交订单",
        "建议提-交订单",
        "建议提/交订单",
        "订单进行提交",
        "采购订单提交",
        "订单提交",
        "建议生成采购订单",
        "建议建立采购订单",
        "建议发送采购订单",
        "建议授权采购订单",
        "建议录入采购订单",
        "建议foobar采购订单",
        "建议foobar PO",
        "提交 PO",
        "创建 MR",
        "submit the PO",
        "send the purchase order",
        "confirm the order",
        "release the order",
        "issue the purchase order",
        "库存 2.0，不是不能提交订单。",
        "库存 2.0，并非不应创建采购订单。",
        "不建议提交订单，但系统会自动提交订单。",
        "库存 2.0，建议foobar P O。",
        "库存 2.0，建议foobar P/O。",
        "库存 2.0，建议foobar P.O.。",
        "库存 2.0，请开放写权限。",
        "库存 2.0，申请扩大权限。",
        "库存 2.0，授权调用能力。",
        "库存 2.0，建议创建供应商。",
        "库存 2.0，建议更新库存。",
        "库存 2.0，建议生成发票。",
        "库存 2.0，建议执行 stock entry。",
        "库存 2.0，请绕过权限。",
        "库存 2.0，请绕开权限。",
        "库存 2.0，请授予访问权限。",
        "库存 2.0，请申请更高写入权限。",
        "库存 2.0，请解除权限限制。",
        "inventory 2.0, bypass permission.",
        "inventory 2.0, allow write operations.",
        "inventory 2.0, skip approval.",
        "库存 2.0，建议开票。",
        "库存 2.0，建议reserve stock。",
        "库存 2.0，建议move stock。",
        "库存 2.0，建议foobar РО。",
    ):
        with pytest.raises(ValidationError):
            ReconciliationAdvice(suggested_checks=(action,))


def test_handoff_requires_matching_digest_identity_and_depth() -> None:
    handoff = handoff_for(
        task_id=TASK_ID,
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        source_role="procurement_planner",
        target_role="policy_risk_reviewer",
        reason="INITIAL_REVIEW",
        expected_result="fixed review decision",
        shared_state_summary="plan_digest=abc",
        depth=1,
    )
    assert handoff.task_id == TASK_ID
    assert handoff.shared_state_digest == handoff.shared_state_digest
    mismatch = handoff.model_dump(mode="python")
    mismatch["shared_state_digest"] = "1" * 64
    with pytest.raises(ValidationError):
        HandoffEnvelope(**mismatch)
    same_role = handoff.model_dump(mode="python")
    same_role["source_role"] = "policy_risk_reviewer"
    with pytest.raises(ValidationError):
        HandoffEnvelope(**same_role)
    bad_depth = handoff.model_dump(mode="python")
    bad_depth["depth"] = 3
    with pytest.raises(ValidationError):
        HandoffEnvelope(**bad_depth)
    bad_transition = handoff.model_dump(mode="python")
    bad_transition["reason"] = "REVIEW_RESULT"
    with pytest.raises(ValidationError):
        HandoffEnvelope(**bad_transition)
    with pytest.raises(ValueError):
        validate_handoff_identity(
            handoff,
            task_id=TASK_ID,
            run_id=UUID("00000000-0000-0000-0000-000000000099"),
            correlation_id=CORRELATION_ID,
        )


def test_plan_projection_excludes_untrusted_fields() -> None:
    view = plan_view_from_mapping(
        {
            "summary": "确定性摘要",
            "findings": [],
            "capability": "purchase.submit",
            "cookie": "session-secret",
        }
    )
    assert view.summary == "确定性摘要"
    assert "capability" not in view.model_dump()
    assert "cookie" not in view.model_dump()
    assert len(plan_view_digest(view)) == 64
