"""Deterministic Memory lifecycle policy tests for Phase 8."""

from typing import cast

import pytest
from agent_runtime.memory import (
    MemoryStateError,
    transition_state,
    validate_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("CANDIDATE", "APPROVED"),
        ("CANDIDATE", "REJECTED"),
        ("CANDIDATE", "EXPIRED"),
        ("CANDIDATE", "DELETED"),
        ("APPROVED", "SUPERSEDED"),
        ("APPROVED", "EXPIRED"),
        ("APPROVED", "DELETED"),
        ("REJECTED", "DELETED"),
        ("SUPERSEDED", "DELETED"),
        ("EXPIRED", "DELETED"),
    ],
)
def test_allowed_memory_transitions(current: str, target: str) -> None:
    validate_transition(current, target)
    assert transition_state(current, target, state_version=4) == (target, 5)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("REJECTED", "APPROVED"),
        ("SUPERSEDED", "APPROVED"),
        ("EXPIRED", "APPROVED"),
        ("DELETED", "CANDIDATE"),
        ("APPROVED", "REJECTED"),
        ("CANDIDATE", "SUPERSEDED"),
        ("DELETED", "DELETED"),
    ],
)
def test_resurrection_and_unlisted_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(MemoryStateError):
        validate_transition(current, target)
    with pytest.raises(MemoryStateError):
        transition_state(current, target, state_version=1)


@pytest.mark.parametrize(
    ("current", "target"),
    [("UNKNOWN", "APPROVED"), ("CANDIDATE", "UNKNOWN"), ("", "")],
)
def test_invalid_state_names_are_rejected(current: str, target: str) -> None:
    with pytest.raises(MemoryStateError):
        validate_transition(current, target)


def test_state_version_increments_exactly_once_and_honors_matching_cas() -> None:
    assert transition_state("CANDIDATE", "APPROVED", state_version=7, expected_version=7) == (
        "APPROVED",
        8,
    )


def test_stale_expected_version_is_rejected_without_a_new_result() -> None:
    current = "CANDIDATE"
    state_version = 7
    with pytest.raises(MemoryStateError):
        transition_state(current, "APPROVED", state_version=state_version, expected_version=6)
    assert current == "CANDIDATE"
    assert state_version == 7


@pytest.mark.parametrize("state_version", [0, -1, True, False, 1.0, "1", None])
def test_invalid_state_versions_are_rejected(state_version: object) -> None:
    with pytest.raises(MemoryStateError):
        transition_state("CANDIDATE", "APPROVED", state_version=cast(int, state_version))


@pytest.mark.parametrize("expected_version", [0, -1, True, False, 1.0, "1"])
def test_invalid_expected_versions_are_rejected_when_supplied(expected_version: object) -> None:
    with pytest.raises(MemoryStateError):
        transition_state(
            "CANDIDATE",
            "APPROVED",
            state_version=1,
            expected_version=cast(int, expected_version),
        )
