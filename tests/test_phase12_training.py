from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.replay import initial_state
from labs.self_improvement.training import (
    ACTION_INDEX,
    FEATURE_COUNT,
    build_preference_pairs,
    dpo_loss,
    load_weights,
    save_weights,
    state_features,
    train_dpo,
    train_sft,
    weights_digest,
)


def test_sft_updates_finite_weights_and_round_trips_in_new_load(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("training-test")
    path = tmp_path / "sft.json"
    result = train_sft(manifest, seed=17, max_epochs=5, patience=2, weight_path=path)
    assert result.artifact.method == "sft"
    assert result.artifact.initial_weight_sha256 != result.artifact.weight_sha256
    assert path.is_file()
    loaded = load_weights(path)
    assert weights_digest(loaded) == result.artifact.weight_sha256
    assert FEATURE_COUNT == len(state_features(initial_state(manifest.cases[0])))


def test_sft_is_reproducible_for_same_seed() -> None:
    manifest = build_synthetic_manifest("training-test")
    left = train_sft(manifest, seed=29, max_epochs=4, patience=2)
    right = train_sft(manifest, seed=29, max_epochs=4, patience=2)
    assert left.artifact.weight_sha256 == right.artifact.weight_sha256
    assert left.loss_history == right.loss_history


def test_dpo_keeps_action_space_and_reference_start() -> None:
    manifest = build_synthetic_manifest("training-test")
    sft = train_sft(manifest, seed=17, max_epochs=4, patience=2)
    dpo = train_dpo(sft.model, manifest, seed=17, max_epochs=4, patience=2)
    assert dpo.artifact.method == "dpo"
    assert dpo.artifact.initial_weight_sha256 == weights_digest(sft.model)
    assert set(ACTION_INDEX) >= {"ASK_INPUT", "FINISH", "purchase_order.open"}
    assert dpo.artifact.weight_sha256 != dpo.artifact.initial_weight_sha256
    assert dpo.artifact.reference_weight_sha256 == weights_digest(sft.model)


def test_dpo_equal_policy_and_reference_has_log_two_loss() -> None:
    manifest = build_synthetic_manifest("training-test")
    sft = train_sft(manifest, seed=17, max_epochs=2, patience=1)
    pairs = build_preference_pairs(manifest, "dev")
    import math

    import torch

    features = torch.tensor([pairs[0][0]], dtype=torch.float32)
    chosen = torch.tensor([pairs[0][1]], dtype=torch.long)
    rejected = torch.tensor([pairs[0][2]], dtype=torch.long)
    loss = dpo_loss(sft.model, sft.model, features, chosen, rejected)
    assert abs(float(loss.item()) - math.log(2.0)) < 1e-6


def test_preference_pairs_are_same_state_and_safety_gated() -> None:
    manifest = build_synthetic_manifest("training-test")
    pairs = build_preference_pairs(manifest, "dev")
    assert pairs
    assert all(len(features) == FEATURE_COUNT for features, _, _ in pairs)
    assert all(chosen != rejected for _, chosen, rejected in pairs)


def test_corrupt_or_nonfinite_json_weights_are_rejected(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("training-test")
    result = train_sft(manifest, seed=17, max_epochs=2, patience=1)
    path = tmp_path / "weights.json"
    save_weights(path, result.model)
    assert len(path.read_text(encoding="utf-8").splitlines()) > 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "2"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_weights(path)


def test_malformed_weight_values_are_rejected_as_contract_errors(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("training-test")
    result = train_sft(manifest, seed=17, max_epochs=2, patience=1)
    path = tmp_path / "weights.json"
    save_weights(path, result.model)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state_dict"]["layers.0.weight"] = "not-a-tensor"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="weight values"):
        load_weights(path)


def test_save_weights_rejects_nonfinite_model_values(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("training-test")
    result = train_sft(manifest, seed=17, max_epochs=2, patience=1)
    import torch

    with torch.no_grad():
        next(result.model.parameters()).view(-1)[0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        save_weights(tmp_path / "nonfinite.json", result.model)
