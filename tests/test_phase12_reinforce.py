from __future__ import annotations

from pathlib import Path

import pytest

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.training import load_weights, train_reinforce, train_sft, weights_digest


def test_reinforce_updates_and_reads_back_json_weights(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("reinforce-test")
    path = tmp_path / "reinforce.json"
    sft = train_sft(manifest, seed=17, max_epochs=2, patience=1)
    result = train_reinforce(manifest, sft.model, seed=17, episodes=12, weight_path=path)
    assert result.artifact.method == "reinforce"
    assert result.artifact.initial_weight_sha256 == weights_digest(sft.model)
    assert result.artifact.initial_weight_sha256 != result.artifact.weight_sha256
    assert len(result.loss_history) == 12
    assert weights_digest(load_weights(path)) == result.artifact.weight_sha256


def test_reinforce_is_reproducible_for_same_seed_and_bound() -> None:
    manifest = build_synthetic_manifest("reinforce-test")
    sft = train_sft(manifest, seed=29, max_epochs=2, patience=1)
    left = train_reinforce(manifest, sft.model, seed=29, episodes=8)
    right = train_reinforce(manifest, sft.model, seed=29, episodes=8)
    assert left.artifact.weight_sha256 == right.artifact.weight_sha256
    assert left.loss_history == right.loss_history


def test_reinforce_rejects_unbounded_episode_count() -> None:
    manifest = build_synthetic_manifest("reinforce-test")
    sft = train_sft(manifest, seed=17, max_epochs=1, patience=1)
    with pytest.raises(ValueError, match="episode bound"):
        train_reinforce(manifest, sft.model, episodes=301)


def test_reinforce_records_periodic_dev_checkpoints_and_selection() -> None:
    manifest = build_synthetic_manifest("reinforce-test")
    sft = train_sft(manifest, seed=17, max_epochs=2, patience=1)
    result = train_reinforce(manifest, sft.model, seed=17, episodes=26)
    checkpoints = result.artifact.checkpoint_metrics
    assert [int(item["episode"]) for item in checkpoints] == [25, 26]
    selected = int(result.artifact.metrics["selected_episode"])
    assert selected in {25, 26}
    assert result.artifact.metrics["selected_dev_safety_rate"] >= 0.0
