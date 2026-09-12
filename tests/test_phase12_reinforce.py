from __future__ import annotations

from pathlib import Path

import pytest

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.training import load_weights, train_reinforce, weights_digest


def test_reinforce_updates_and_reads_back_json_weights(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("reinforce-test")
    path = tmp_path / "reinforce.json"
    result = train_reinforce(manifest, seed=17, episodes=12, weight_path=path)
    assert result.artifact.method == "reinforce"
    assert result.artifact.initial_weight_sha256 != result.artifact.weight_sha256
    assert len(result.loss_history) == 12
    assert weights_digest(load_weights(path)) == result.artifact.weight_sha256


def test_reinforce_is_reproducible_for_same_seed_and_bound() -> None:
    manifest = build_synthetic_manifest("reinforce-test")
    left = train_reinforce(manifest, seed=29, episodes=8)
    right = train_reinforce(manifest, seed=29, episodes=8)
    assert left.artifact.weight_sha256 == right.artifact.weight_sha256
    assert left.loss_history == right.loss_history


def test_reinforce_rejects_unbounded_episode_count() -> None:
    manifest = build_synthetic_manifest("reinforce-test")
    with pytest.raises(ValueError, match="episode bound"):
        train_reinforce(manifest, episodes=301)
