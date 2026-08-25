"""Run 状态机确定性转换测试 (SPEC §8.1)。

纯 Python 单测, 不依赖 Frappe: 非法转换必须 fail closed。
"""

import pytest

from synora_agentic_erp.agent.state_machine import (
    CANCELLABLE_STATES,
    RUN_STATES,
    validate_transition,
)
from synora_agentic_erp.gateway.contract import GatewayFault

_LEGAL = [
    ("CREATED", "ANALYZING"),
    ("CREATED", "CANCELLED"),
    ("ANALYZING", "PROPOSED"),
    ("ANALYZING", "CANCELLED"),
    # 验收修复: 分析失败回退 CREATED (可重试, 受控路径执行)。
    ("ANALYZING", "CREATED"),
    ("PROPOSED", "AWAITING_APPROVAL"),
    ("PROPOSED", "SUCCEEDED"),
    ("AWAITING_APPROVAL", "DECLINED"),
    ("AWAITING_APPROVAL", "EXPIRED"),
    ("AWAITING_APPROVAL", "EXECUTING"),
    ("EXECUTING", "SUCCEEDED"),
    ("EXECUTING", "FAILED"),
    ("EXECUTING", "RECONCILIATION_REQUIRED"),
    ("RECONCILIATION_REQUIRED", "SUCCEEDED"),
    ("RECONCILIATION_REQUIRED", "FAILED"),
]

_ILLEGAL = [
    ("CREATED", "SUCCEEDED"),
    ("CREATED", "PROPOSED"),
    ("ANALYZING", "SUCCEEDED"),
    ("PROPOSED", "EXECUTING"),
    ("AWAITING_APPROVAL", "CANCELLED"),
    ("EXECUTING", "CANCELLED"),
    ("SUCCEEDED", "CREATED"),
    ("SUCCEEDED", "ANALYZING"),
    ("CANCELLED", "ANALYZING"),
    ("FAILED", "RETRY"),
    ("UNKNOWN", "CREATED"),
]


def test_legal_transitions_are_accepted() -> None:
    for current, target in _LEGAL:
        validate_transition(current, target)  # 不抛即通过


def test_illegal_transitions_fail_closed() -> None:
    for current, target in _ILLEGAL:
        with pytest.raises(GatewayFault):
            validate_transition(current, target)


def test_terminal_states_have_no_outgoing_edges() -> None:
    for state in ("SUCCEEDED", "FAILED", "CANCELLED", "DECLINED", "EXPIRED"):
        with pytest.raises(GatewayFault):
            validate_transition(state, "CREATED")
        with pytest.raises(GatewayFault):
            validate_transition(state, "ANALYZING")


def test_all_states_are_declared() -> None:
    assert "CREATED" in RUN_STATES
    assert "RECONCILIATION_REQUIRED" in RUN_STATES
    assert len(RUN_STATES) == len(set(RUN_STATES))


def test_cancellable_states_match_spec() -> None:
    assert CANCELLABLE_STATES == frozenset({"CREATED", "ANALYZING"})
