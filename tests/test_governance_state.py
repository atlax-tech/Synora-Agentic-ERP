"""Pure state-machine tests for governed ProposedAction records."""

import pytest

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.state import (
    ACTION_STATES,
    transition_state,
    validate_transition,
)


def test_action_state_table_contains_only_governed_states() -> None:
    assert ACTION_STATES == (
        "DRAFT",
        "INVALID",
        "POLICY_REJECTED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "DECLINED",
        "EXPIRED",
        "EXECUTED",
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DRAFT", "INVALID"),
        ("DRAFT", "POLICY_REJECTED"),
        ("DRAFT", "AWAITING_APPROVAL"),
        ("AWAITING_APPROVAL", "APPROVED"),
        ("AWAITING_APPROVAL", "DECLINED"),
        ("AWAITING_APPROVAL", "EXPIRED"),
        ("APPROVED", "EXECUTED"),
        ("APPROVED", "EXPIRED"),
    ],
)
def test_legal_transition_increments_version(current: str, target: str) -> None:
    assert transition_state(current, target, state_version=4) == (target, 5)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DRAFT", "APPROVED"),
        ("DRAFT", "EXECUTED"),
        ("AWAITING_APPROVAL", "EXECUTED"),
        ("APPROVED", "DECLINED"),
        ("EXECUTED", "EXPIRED"),
    ],
)
def test_illegal_transition_is_typed_conflict(current: str, target: str) -> None:
    with pytest.raises(GatewayFault) as error:
        validate_transition(current, target)
    assert error.value.code == "CONFLICT"


def test_compare_and_set_version_rejects_stale_transition_without_mutation() -> None:
    with pytest.raises(GatewayFault) as error:
        transition_state("DRAFT", "AWAITING_APPROVAL", state_version=4, expected_version=3)
    assert error.value.code == "CONFLICT"
