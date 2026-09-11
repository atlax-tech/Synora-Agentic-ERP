from __future__ import annotations

import pytest

from labs.web_gui.recovery import ProgressGuard, RecoveryFailure


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
