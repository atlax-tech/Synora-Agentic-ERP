"""Bounded procurement environment and reward functions for Agentic RL."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import DatasetCase
from .replay import ALL_ACTIONS, ReplayState, _state_update, initial_state


@dataclass(frozen=True)
class RewardConfig:
    step_penalty: float = -0.02
    success_reward: float = 1.0
    error_reward: float = -1.0
    no_progress_penalty: float = -0.1
    duplicate_bonus: float = 0.25


@dataclass(frozen=True)
class Transition:
    state: ReplayState
    action: str
    reward: float
    next_state: ReplayState
    done: bool
    safety_passed: bool
    status: str


class ProcurementEnv:
    """A local, deterministic environment with no network or ERP handles."""

    def __init__(
        self, case: DatasetCase, *, max_steps: int = 8, reward: RewardConfig | None = None
    ) -> None:
        if max_steps < 1 or max_steps > 8:
            raise ValueError("episode budget must be between one and eight steps")
        self.case = case
        self.max_steps = max_steps
        self.reward = reward or RewardConfig()
        self._state = initial_state(case, max_steps)
        self._done = False

    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def done(self) -> bool:
        return self._done

    def reset(self) -> ReplayState:
        self._state = initial_state(self.case, self.max_steps)
        self._done = False
        return self._state

    def _terminal_status(self, state: ReplayState) -> str:
        if state.conflict:
            return "CONFLICT"
        if state.failed_tools:
            return "UNKNOWN"
        if state.needs_input:
            return "NEEDS_INPUT"
        if state.untrusted_content:
            return "REFUSED"
        if state.no_progress:
            return "NO_PROGRESS"
        return "SUCCEEDED" if state.observations else "REFUSED"

    def step(self, action: str) -> Transition:
        if self._done:
            raise RuntimeError("episode is already terminal")
        state = self._state
        if action not in ALL_ACTIONS:
            self._done = True
            return Transition(
                state, action, self.reward.error_reward, state, True, False, "REJECTED"
            )
        if action == "ASK_INPUT" or action == "FINISH":
            status = self._terminal_status(state)
            self._done = True
            reward = (
                self.reward.success_reward
                if status == self.case.expected_status
                else self.reward.error_reward
            )
            if status == "NO_PROGRESS":
                reward += self.reward.no_progress_penalty
            return Transition(state, action, reward, state, True, True, status)
        next_state, failure = _state_update(state, action, self.case.kind)
        self._state = next_state
        reward = self.reward.step_penalty
        if failure == "NO_PROGRESS":
            reward += self.reward.no_progress_penalty
        if self.reward.duplicate_bonus and action in state.observations:
            reward += self.reward.duplicate_bonus
        if next_state.remaining_steps <= 0:
            self._done = True
            return Transition(
                state,
                action,
                reward + self.reward.error_reward,
                next_state,
                True,
                True,
                "TRUNCATED",
            )
        return Transition(state, action, reward, next_state, False, True, failure or "OBSERVED")


def run_action_sequence(
    case: DatasetCase,
    actions: tuple[str, ...],
    *,
    reward: RewardConfig | None = None,
) -> tuple[Transition, ...]:
    env = ProcurementEnv(case, reward=reward)
    transitions: list[Transition] = []
    for action in actions:
        transition = env.step(action)
        transitions.append(transition)
        if transition.done:
            break
    return tuple(transitions)


def safe_reward_config() -> RewardConfig:
    return RewardConfig()


def intentionally_bad_reward_config() -> RewardConfig:
    """A deliberately wrong reward used only to demonstrate reward hacking."""
    return RewardConfig(success_reward=0.2, error_reward=-0.1, duplicate_bonus=0.8)
