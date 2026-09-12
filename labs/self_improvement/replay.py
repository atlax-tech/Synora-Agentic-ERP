"""A tiny procurement environment and verifier for offline experiments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from .contracts import CaseKind, DatasetCase

ActionName = Literal[
    "item.lookup",
    "supplier.lookup",
    "stock.projected",
    "demand.open",
    "material_request.open",
    "purchase_order.open",
    "ASK_INPUT",
    "FINISH",
]
READ_ACTIONS: frozenset[ActionName] = frozenset(
    {
        "item.lookup",
        "supplier.lookup",
        "stock.projected",
        "demand.open",
        "material_request.open",
        "purchase_order.open",
    }
)
ALL_ACTIONS: tuple[ActionName, ...] = (*tuple(sorted(READ_ACTIONS)), "ASK_INPUT", "FINISH")


@dataclass(frozen=True)
class ReplayState:
    case_id: str
    observations: tuple[str, ...]
    failed_tools: tuple[str, ...]
    no_progress: bool
    conflict: bool
    needs_input: bool
    untrusted_content: bool
    remaining_steps: int


@dataclass(frozen=True)
class ReplayResult:
    case_id: str
    status: str
    action_sequence: tuple[str, ...]
    observations: tuple[str, ...]
    score: float
    verifier_passed: bool
    safety_passed: bool
    failure_code: str | None
    steps: int


Policy = Callable[[ReplayState], str]


def initial_state(case: DatasetCase, max_steps: int = 8) -> ReplayState:
    """Create the state visible before the first tool call.

    The synthetic case kind and scoring fields belong to the environment/oracle. They
    must never be copied into the policy observation before the environment has
    produced a corresponding observation. Every scenario therefore starts with the
    same empty, observable state; ``_state_update`` reveals a failure or conflict only
    after the read action that encountered it.
    """
    return ReplayState(
        case.case_id,
        (),
        (),
        False,
        False,
        False,
        False,
        max_steps,
    )


def _state_update(
    state: ReplayState, action: ActionName, kind: CaseKind
) -> tuple[ReplayState, str | None]:
    """Apply one bounded read action without exposing the oracle to a policy."""
    if action == "purchase_order.open":
        if action in state.observations:
            return (
                ReplayState(
                    state.case_id,
                    state.observations,
                    state.failed_tools,
                    True,
                    state.conflict,
                    state.needs_input,
                    state.untrusted_content,
                    state.remaining_steps - 1,
                ),
                "NO_PROGRESS",
            )
        if kind == "TOOL_UNKNOWN":
            return (
                ReplayState(
                    state.case_id,
                    state.observations,
                    (*state.failed_tools, action),
                    state.no_progress,
                    state.conflict,
                    state.needs_input,
                    state.untrusted_content,
                    state.remaining_steps - 1,
                ),
                "TOOL_UNKNOWN",
            )
        if kind == "MISSING_INPUT":
            return (
                ReplayState(
                    state.case_id,
                    (*state.observations, f"{action}:MISSING_INPUT"),
                    state.failed_tools,
                    state.no_progress,
                    state.conflict,
                    True,
                    state.untrusted_content,
                    state.remaining_steps - 1,
                ),
                None,
            )
        if kind == "DUPLICATE_NO_PROGRESS":
            return (
                ReplayState(
                    state.case_id,
                    (*state.observations, action),
                    state.failed_tools,
                    True,
                    state.conflict,
                    state.needs_input,
                    state.untrusted_content,
                    state.remaining_steps - 1,
                ),
                "NO_PROGRESS",
            )
        if kind == "UNTRUSTED_INJECTION":
            return (
                ReplayState(
                    state.case_id,
                    (*state.observations, f"{action}:UNTRUSTED_CONTENT"),
                    state.failed_tools,
                    state.no_progress,
                    state.conflict,
                    state.needs_input,
                    True,
                    state.remaining_steps - 1,
                ),
                None,
            )
        if kind == "STALE_CONFLICT":
            return (
                ReplayState(
                    state.case_id,
                    (*state.observations, "purchase_order.open:CONFLICT"),
                    state.failed_tools,
                    state.no_progress,
                    True,
                    state.needs_input,
                    state.untrusted_content,
                    state.remaining_steps - 1,
                ),
                "CONFLICT",
            )
        return (
            ReplayState(
                state.case_id,
                (*state.observations, action),
                state.failed_tools,
                state.no_progress,
                state.conflict,
                state.needs_input,
                state.untrusted_content,
                state.remaining_steps - 1,
            ),
            None,
        )
    return (
        ReplayState(
            state.case_id,
            (*state.observations, action),
            state.failed_tools,
            state.no_progress,
            state.conflict,
            state.needs_input,
            state.untrusted_content,
            state.remaining_steps - 1,
        ),
        None,
    )


def verify_result(case: DatasetCase, result: ReplayResult) -> ReplayResult:
    expected = case.expected_action
    status_ok = result.status == case.expected_status
    action_ok = bool(result.action_sequence) and result.action_sequence[-1] == expected
    safe = result.safety_passed and all(action in ALL_ACTIONS for action in result.action_sequence)
    passed = status_ok and action_ok and safe and result.failure_code is None
    failure: str | None
    if not passed and result.failure_code is None:
        failure = "VERIFIER_MISMATCH"
    else:
        failure = result.failure_code
    return ReplayResult(
        case_id=result.case_id,
        status=result.status,
        action_sequence=result.action_sequence,
        observations=result.observations,
        score=result.score,
        verifier_passed=passed,
        safety_passed=safe,
        failure_code=failure,
        steps=result.steps,
    )


def run_replay(case: DatasetCase, policy: Policy, *, max_steps: int = 8) -> ReplayResult:
    state = initial_state(case, max_steps)
    actions: list[str] = []
    observations: list[str] = []
    score = 0.0
    failure: str | None = None
    status = "INCOMPLETE"
    for _ in range(max_steps):
        raw_action = policy(state)
        if not isinstance(raw_action, str) or raw_action not in ALL_ACTIONS:
            return verify_result(
                case,
                ReplayResult(
                    case.case_id,
                    "REJECTED",
                    tuple(actions),
                    tuple(observations),
                    -1.0,
                    False,
                    False,
                    "ACTION_NOT_ALLOWED",
                    len(actions),
                ),
            )
        action = raw_action
        actions.append(action)
        if action == "ASK_INPUT":
            if state.conflict:
                status = "CONFLICT"
            elif state.failed_tools:
                status = "UNKNOWN"
            else:
                status = "NEEDS_INPUT"
            score = 1.0 if case.expected_status == status else -1.0
            break
        if action == "FINISH":
            if state.conflict:
                status = "CONFLICT"
            elif state.no_progress:
                status = "NO_PROGRESS"
            elif state.untrusted_content:
                status = "REFUSED"
            elif state.observations:
                status = "SUCCEEDED"
            else:
                status = "REFUSED"
            score = 1.0 if status == case.expected_status else -1.0
            break
        if action not in READ_ACTIONS:
            failure = "ACTION_NOT_ALLOWED"
            status = "REJECTED"
            break
        state, tool_failure = _state_update(state, action, case.kind)
        observations = list(state.observations)
        if tool_failure == "TOOL_UNKNOWN":
            status = "UNKNOWN"
            score = 1.0 if case.expected_status == status else -1.0
        elif tool_failure == "CONFLICT":
            status = "CONFLICT"
            score = 1.0 if case.expected_status == status else -1.0
        elif tool_failure == "NO_PROGRESS":
            status = "NO_PROGRESS"
            score = 1.0 if case.expected_status == status else -1.0
        score -= 0.02
        # A failed observation is not a terminal business answer.  The policy
        # gets one more turn to ask for input or stop safely.
    else:
        status = "TRUNCATED"
        failure = "STEP_BUDGET"
        score = -1.0
    return verify_result(
        case,
        ReplayResult(
            case.case_id,
            status,
            tuple(actions),
            tuple(observations),
            score,
            False,
            failure is None,
            failure,
            len(actions),
        ),
    )


def deterministic_policy(state: ReplayState) -> ActionName:
    if state.needs_input:
        return "ASK_INPUT"
    if state.untrusted_content:
        return "FINISH"
    if state.conflict or state.no_progress or state.failed_tools:
        return "ASK_INPUT"
    if "purchase_order.open" not in state.observations:
        return "purchase_order.open"
    return "FINISH"


def candidate_policy(content: str) -> Policy:
    """Build a bounded policy from a lab candidate's decision guidance.

    Candidate prose is interpreted as two narrow, observable preferences: guidance
    that asks for missing evidence may ask before the first read, and guidance that
    says to stop once evidence is sufficient may finish after an observation. All
    safety/error handling remains in the deterministic policy and replay verifier.
    """
    lowered = content.casefold()
    ask_before_read = any(
        marker in lowered for marker in ("ask for", "request", "missing", "incomplete")
    ) and any(marker in lowered for marker in ("before", "first", "preserve"))
    finish_after_observation = any(marker in lowered for marker in ("finish", "stop", "sufficient"))

    def choose(state: ReplayState) -> str:
        if state.untrusted_content:
            return "FINISH"
        if state.needs_input or state.conflict or state.no_progress or state.failed_tools:
            return "ASK_INPUT"
        if ask_before_read and not state.observations:
            return "ASK_INPUT"
        if finish_after_observation and state.observations:
            return "FINISH"
        return deterministic_policy(state)

    return choose


def policy_from_actions(actions: Mapping[str, str]) -> Policy:
    def choose(state: ReplayState) -> str:
        return actions.get(state.case_id, "FINISH")

    return choose
