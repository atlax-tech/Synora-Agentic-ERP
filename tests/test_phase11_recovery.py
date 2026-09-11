from __future__ import annotations

import time

import pytest

from labs.web_gui.recovery import ProgressGuard, RecoveryFailure, run_with_deadline


def test_progress_guard_stops_repeated_observation_action_pairs() -> None:
    guard = ProgressGuard(max_actions=2)
    guard.record("page-v1", "click-search")
    with pytest.raises(RecoveryFailure, match="NO_PROGRESS"):
        guard.record("page-v1", "click-search")


def test_progress_guard_has_a_hard_action_budget() -> None:
    guard = ProgressGuard(max_actions=1)
    guard.record("page-v1", "wait")
    with pytest.raises(RecoveryFailure, match="ACTION_BUDGET"):
        guard.record("page-v2", "wait")


def test_run_with_deadline_returns_completed_call() -> None:
    assert run_with_deadline(lambda: "ready", 0.2) == "ready"


def test_run_with_deadline_stops_waiting_for_hung_call() -> None:
    started = time.monotonic()
    with pytest.raises(RecoveryFailure, match="MODEL_TIMEOUT"):
        run_with_deadline(lambda: time.sleep(0.2), 0.01)
    assert time.monotonic() - started < 0.15
