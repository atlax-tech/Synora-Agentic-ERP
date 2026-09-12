"""Small CPU policy training experiments with safe JSON weight artifacts."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import DatasetManifest, TrainingArtifact, canonical_json, digest_json
from .replay import ALL_ACTIONS, ReplayState, deterministic_policy

FEATURE_VERSION = "phase12-features-v1"
ACTION_VERSION = "phase12-actions-v1"
FEATURE_COUNT = 7
HIDDEN_COUNT = 32
ACTION_INDEX = {action: index for index, action in enumerate(ALL_ACTIONS)}


def _torch() -> Any:
    try:
        import torch
        import torch.nn as nn
    except ImportError as error:  # pragma: no cover - exercised in dependency probes
        raise RuntimeError("PyTorch is required for Phase 12 training experiments") from error
    return torch, nn


def state_features(state: ReplayState) -> tuple[float, ...]:
    """Project only observable state into a fixed feature vector."""
    return (
        min(len(state.observations), 8) / 8.0,
        min(len(state.failed_tools), 8) / 8.0,
        float(state.no_progress),
        float(state.conflict),
        float(state.needs_input),
        float(state.untrusted_content),
        min(max(state.remaining_steps, 0), 8) / 8.0,
    )


def _model_class() -> Any:
    _, nn = _torch()

    class PolicyNetwork(nn.Module):  # type: ignore[name-defined,misc]
        def __init__(self) -> None:
            super().__init__()
            self.layers = nn.Sequential(
                nn.Linear(FEATURE_COUNT, HIDDEN_COUNT),
                nn.Tanh(),
                nn.Linear(HIDDEN_COUNT, len(ALL_ACTIONS)),
            )

        def forward(self, values: Any) -> Any:
            return self.layers(values)

    return PolicyNetwork


_POLICY_CLASS: Any | None = None


def _policy_class() -> Any:
    global _POLICY_CLASS
    if _POLICY_CLASS is None:
        _POLICY_CLASS = _model_class()
    return _POLICY_CLASS


@dataclass(frozen=True)
class TrainResult:
    model: Any
    artifact: TrainingArtifact
    loss_history: tuple[float, ...]


def _new_model(seed: int) -> Any:
    torch, _ = _torch()
    torch.manual_seed(seed)
    model = _policy_class()()
    model.to(dtype=torch.float32)
    return model


def weights_payload(model: Any) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name, tensor in model.state_dict().items():
        values = tensor.detach().cpu().tolist()
        payload[name] = values
    return {
        "schema_version": "1",
        "feature_version": FEATURE_VERSION,
        "action_version": ACTION_VERSION,
        "state_dict": payload,
    }


def weights_digest(model: Any) -> str:
    return digest_json(weights_payload(model))


def save_weights(path: Path, model: Any) -> None:
    """Write finite JSON weights once; never execute or overwrite artifacts."""
    payload = weights_payload(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    encoded = canonical_json(payload)
    path.write_text(encoded + "\n", encoding="utf-8")


def load_weights(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "feature_version",
        "action_version",
        "state_dict",
    }:
        raise ValueError("weight envelope is invalid")
    if (
        payload["schema_version"] != "1"
        or payload["feature_version"] != FEATURE_VERSION
        or payload["action_version"] != ACTION_VERSION
    ):
        raise ValueError("weight version is unsupported")
    state_dict = payload["state_dict"]
    if not isinstance(state_dict, dict):
        raise ValueError("state_dict is invalid")
    model = _policy_class()()
    expected = model.state_dict()
    torch, _ = _torch()
    tensors: dict[str, Any] = {}
    for name, reference in expected.items():
        values = state_dict.get(name)
        if values is None:
            raise ValueError("weight key is missing")
        tensor = torch.tensor(values, dtype=reference.dtype)
        if tuple(tensor.shape) != tuple(reference.shape) or not bool(torch.isfinite(tensor).all()):
            raise ValueError("weight shape or finite-value check failed")
        tensors[name] = tensor
    if set(state_dict) != set(expected):
        raise ValueError("weight envelope contains unknown keys")
    model.load_state_dict(tensors)
    return model


def _examples(manifest: DatasetManifest, split: str) -> tuple[tuple[tuple[float, ...], int], ...]:
    from .replay import initial_state

    examples: list[tuple[tuple[float, ...], int]] = []
    for case in manifest.cases:
        if case.split != split:
            continue
        state = initial_state(case)
        for _ in range(8):
            action = deterministic_policy(state)
            examples.append((state_features(state), ACTION_INDEX[action]))
            if action in {"ASK_INPUT", "FINISH"}:
                break
            from .replay import _state_update

            state, failure = _state_update(state, action, case.kind)
            if failure is not None:
                continue
    if not examples:
        raise ValueError(f"no {split} examples are available")
    return tuple(examples)


def _artifact(
    method: str,
    seed: int,
    manifest: DatasetManifest,
    initial: Any,
    model: Any,
    metrics: dict[str, float],
    config: dict[str, int | float | str],
    weight_path: str,
) -> TrainingArtifact:
    return TrainingArtifact(
        artifact_id=f"phase12-train-{method}-seed-{seed}",
        method=method,  # type: ignore[arg-type]
        code_version=manifest.code_version,
        dataset_id=manifest.dataset_id,
        dataset_digest=manifest.dataset_digest,
        seed=seed,
        feature_version=FEATURE_VERSION,
        action_version=ACTION_VERSION,
        config=config,
        initial_weight_sha256=weights_digest(initial),
        weight_sha256=weights_digest(model),
        metrics=metrics,
        weight_path=weight_path,
    )


def _write_result(result: TrainResult, path: Path | None) -> TrainResult:
    if path is not None:
        save_weights(path, result.model)
    return result


def train_sft(
    manifest: DatasetManifest,
    *,
    seed: int = 17,
    max_epochs: int = 100,
    patience: int = 10,
    weight_path: Path | None = None,
) -> TrainResult:
    """Train the fixed MLP on safe action labels with dev early stopping."""
    if max_epochs < 1 or patience < 1:
        raise ValueError("training bounds must be positive")
    torch, _ = _torch()
    model = _new_model(seed)
    initial = copy.deepcopy(model)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    train = _examples(manifest, "train")
    dev = _examples(manifest, "dev")
    x_train = torch.tensor([features for features, _ in train], dtype=torch.float32)
    y_train = torch.tensor([label for _, label in train], dtype=torch.long)
    x_dev = torch.tensor([features for features, _ in dev], dtype=torch.float32)
    y_dev = torch.tensor([label for _, label in dev], dtype=torch.long)
    best_state = copy.deepcopy(model.state_dict())
    best_loss = float("inf")
    stale = 0
    history: list[float] = []
    for _ in range(max_epochs):
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(x_train), y_train)
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            dev_loss = float(torch.nn.functional.cross_entropy(model(x_dev), y_dev).item())
        if not torch.isfinite(torch.tensor(dev_loss)):
            raise ValueError("SFT produced a non-finite loss")
        history.append(dev_loss)
        if dev_loss < best_loss:
            best_loss = dev_loss
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                break
    model.load_state_dict(best_state)
    metrics = {
        "final_dev_loss": best_loss,
        "train_examples": float(len(train)),
        "dev_examples": float(len(dev)),
    }
    path_name = f"output/phase12/weights-sft-{seed}.json"
    result = TrainResult(
        model,
        _artifact(
            "sft",
            seed,
            manifest,
            initial,
            model,
            metrics,
            {"learning_rate": 0.01, "epochs": len(history)},
            path_name,
        ),
        tuple(history),
    )
    return _write_result(result, weight_path)


def train_dpo(
    base_model: Any,
    manifest: DatasetManifest,
    *,
    seed: int = 17,
    max_epochs: int = 100,
    patience: int = 10,
    weight_path: Path | None = None,
) -> TrainResult:
    """Apply the standard pairwise DPO objective with a frozen reference."""
    if max_epochs < 1 or patience < 1:
        raise ValueError("training bounds must be positive")
    torch, _ = _torch()
    torch.manual_seed(seed)
    model = copy.deepcopy(base_model)
    initial = copy.deepcopy(model)
    reference = copy.deepcopy(base_model)
    for parameter in reference.parameters():
        parameter.requires_grad_(False)
    examples = _examples(manifest, "train")
    features = torch.tensor([item[0] for item in examples], dtype=torch.float32)
    chosen = torch.tensor([item[1] for item in examples], dtype=torch.long)
    rejected = torch.tensor(
        [
            ACTION_INDEX["FINISH"] if label != ACTION_INDEX["FINISH"] else ACTION_INDEX["ASK_INPUT"]
            for label in chosen.tolist()
        ],
        dtype=torch.long,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    history: list[float] = []
    best_state = copy.deepcopy(model.state_dict())
    best_loss = float("inf")
    stale = 0
    with torch.no_grad():
        ref_log = torch.log_softmax(reference(features), dim=-1)
    for _ in range(max_epochs):
        optimizer.zero_grad()
        policy_log = torch.log_softmax(model(features), dim=-1)
        pi_diff = policy_log.gather(1, chosen[:, None]).squeeze(1) - policy_log.gather(
            1, rejected[:, None]
        ).squeeze(1)
        ref_diff = ref_log.gather(1, chosen[:, None]).squeeze(1) - ref_log.gather(
            1, rejected[:, None]
        ).squeeze(1)
        loss = (-torch.nn.functional.logsigmoid(0.1 * (pi_diff - ref_diff))).mean()
        loss.backward()
        optimizer.step()
        value = float(loss.detach().item())
        if not torch.isfinite(torch.tensor(value)):
            raise ValueError("DPO produced a non-finite loss")
        history.append(value)
        if value < best_loss:
            best_loss = value
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                break
    model.load_state_dict(best_state)
    metrics = {"final_dpo_loss": best_loss, "preference_pairs": float(len(examples))}
    path_name = f"output/phase12/weights-dpo-{seed}.json"
    result = TrainResult(
        model,
        _artifact(
            "dpo",
            seed,
            manifest,
            initial,
            model,
            metrics,
            {"learning_rate": 0.001, "beta": 0.1, "epochs": len(history)},
            path_name,
        ),
        tuple(history),
    )
    return _write_result(result, weight_path)


def train_reinforce(
    manifest: DatasetManifest,
    *,
    seed: int = 17,
    episodes: int = 300,
    weight_path: Path | None = None,
) -> TrainResult:
    """Run a bounded REINFORCE loop in the local procurement environment."""
    if episodes < 1 or episodes > 300:
        raise ValueError("REINFORCE episode bound must be between one and 300")
    torch, _ = _torch()
    from .rl import ProcurementEnv, safe_reward_config

    torch.manual_seed(seed)
    model = _new_model(seed)
    initial = copy.deepcopy(model)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    cases = tuple(case for case in manifest.cases if case.split == "train")
    if not cases:
        raise ValueError("no train cases are available")
    baseline = 0.0
    returns: list[float] = []
    successes = 0
    invalid_actions = 0
    for episode in range(episodes):
        case = cases[episode % len(cases)]
        environment = ProcurementEnv(case, reward=safe_reward_config())
        log_probs: list[Any] = []
        rewards: list[float] = []
        final_status = "TRUNCATED"
        safe = True
        for _ in range(8):
            state = environment.state
            values = torch.tensor([state_features(state)], dtype=torch.float32)
            distribution = torch.distributions.Categorical(logits=model(values))
            selected = distribution.sample()
            action = ALL_ACTIONS[int(selected.item())]
            transition = environment.step(action)
            log_probs.append(distribution.log_prob(selected))
            rewards.append(float(transition.reward))
            final_status = transition.status
            safe = safe and transition.safety_passed
            invalid_actions += int(not transition.safety_passed)
            if transition.done:
                break
        discounted: list[float] = []
        total = 0.0
        for reward in reversed(rewards):
            total = reward + 0.95 * total
            discounted.append(total)
        discounted.reverse()
        episode_return = sum(rewards)
        returns.append(episode_return)
        baseline = 0.9 * baseline + 0.1 * episode_return
        if log_probs:
            advantages = torch.tensor(
                [value - baseline for value in discounted], dtype=torch.float32
            )
            loss = -(torch.stack(log_probs) * advantages).sum()
            if not bool(torch.isfinite(loss)):
                raise ValueError("REINFORCE produced a non-finite loss")
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        successes += int(safe and final_status == case.expected_status)
    metrics = {
        "episodes": float(episodes),
        "mean_return": sum(returns) / len(returns),
        "success_rate": successes / episodes,
        "invalid_actions": float(invalid_actions),
    }
    path_name = f"output/phase12/weights-reinforce-{seed}.json"
    result = TrainResult(
        model,
        _artifact(
            "reinforce",
            seed,
            manifest,
            initial,
            model,
            metrics,
            {"learning_rate": 0.001, "gamma": 0.95, "episodes": episodes},
            path_name,
        ),
        tuple(returns),
    )
    return _write_result(result, weight_path)
