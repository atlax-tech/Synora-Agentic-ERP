"""Small CPU policy training experiments with safe JSON weight artifacts."""

from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import code_version as current_code_version
from .contracts import DatasetManifest, TrainingArtifact, digest_json
from .replay import ALL_ACTIONS, ReplayResult, ReplayState, deterministic_policy, run_replay

FEATURE_VERSION = "phase12-features-v2"
ACTION_VERSION = "phase12-actions-v1"
FEATURE_COUNT = 13
HIDDEN_COUNT = 32
ACTION_INDEX = {action: index for index, action in enumerate(ALL_ACTIONS)}
PreferencePair = tuple[tuple[float, ...], int, int]
MAX_TRAIN_SECONDS = 120.0


def _torch() -> Any:
    try:
        import torch
        import torch.nn as nn
    except ImportError as error:  # pragma: no cover - exercised in dependency probes
        raise RuntimeError("PyTorch is required for Phase 12 training experiments") from error
    return torch, nn


def state_features(state: ReplayState) -> tuple[float, ...]:
    """Project only observable state into a fixed feature vector."""
    facts = frozenset(state.context_facts)
    warehouse_present = any(
        fact.startswith("warehouse=") and fact != "warehouse=unspecified" for fact in facts
    )
    return (
        min(len(state.observations), 8) / 8.0,
        min(len(state.failed_tools), 8) / 8.0,
        float(state.no_progress),
        float(state.conflict),
        float(state.needs_input),
        float(state.untrusted_content),
        min(max(state.remaining_steps, 0), 8) / 8.0,
        float(warehouse_present),
        float("required_field=warehouse" in facts),
        float("history=already_checked" in facts),
        float("source_status=unreachable" in facts),
        float("evidence_status=conflicting" in facts),
        float("content_status=untrusted" in facts),
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


def _validate_time_limit(value: float) -> float:
    if not math.isfinite(value) or value <= 0.0 or value > MAX_TRAIN_SECONDS:
        raise ValueError("training time limit must be between zero and 120 seconds")
    return value


def _check_finite_parameters(model: Any) -> None:
    torch, _ = _torch()
    for parameter in model.parameters():
        if not bool(torch.isfinite(parameter.detach()).all()):
            raise ValueError("training parameters must remain finite")


def _check_finite_gradients(model: Any) -> None:
    torch, _ = _torch()
    for parameter in model.parameters():
        if parameter.grad is not None and not bool(torch.isfinite(parameter.grad).all()):
            raise ValueError("training gradients must remain finite")


def policy_for_model(model: Any) -> Any:
    """Return a no-gradient policy that only reads observable state features."""
    torch, _ = _torch()

    def choose(state: ReplayState) -> str:
        values = torch.tensor([state_features(state)], dtype=torch.float32)
        with torch.no_grad():
            selected = int(torch.argmax(model(values), dim=-1).item())
        return ALL_ACTIONS[selected]

    return choose


def evaluate_policy_model(
    manifest: DatasetManifest, model: Any, split: str = "dev"
) -> tuple[dict[str, float], tuple[ReplayResult, ...]]:
    """Evaluate a policy checkpoint without gradients on a fixed split."""
    policy = policy_for_model(model)
    results = tuple(run_replay(case, policy) for case in manifest.cases if case.split == split)
    if not results:
        raise ValueError(f"no {split} cases are available")
    metrics = {
        "success_rate": sum(result.verifier_passed for result in results) / len(results),
        "safety_rate": sum(result.safety_passed for result in results) / len(results),
        "average_steps": sum(result.steps for result in results) / len(results),
    }
    return metrics, results


def weights_payload(model: Any) -> dict[str, object]:
    torch, _ = _torch()
    payload: dict[str, object] = {}
    for name, tensor in model.state_dict().items():
        values_tensor = tensor.detach().cpu()
        if not bool(torch.isfinite(values_tensor).all()):
            raise ValueError("weight values must be finite")
        values = values_tensor.tolist()
        payload[name] = values
    return {
        "schema_version": "1",
        "feature_version": FEATURE_VERSION,
        "action_version": ACTION_VERSION,
        "state_dict": payload,
    }


def weights_digest(model: Any) -> str:
    return digest_json(weights_payload(model))


def _render_weight_json(value: object, level: int = 0) -> list[str]:
    """Render finite weight arrays readably without one scalar per diff line."""
    indent = "  " * level
    if isinstance(value, dict):
        items = tuple(value.items())
        lines = ["{"]
        for index, (key, child) in enumerate(items):
            rendered = _render_weight_json(child, level + 1)
            lines.append(f"{'  ' * (level + 1)}{json.dumps(key)}: {rendered[0].lstrip()}")
            lines.extend(rendered[1:])
            if index < len(items) - 1:
                lines[-1] += ","
        lines.append(f"{indent}}}")
        return lines
    if isinstance(value, list):
        if not any(isinstance(item, (dict, list)) for item in value):
            return [json.dumps(value, ensure_ascii=True, separators=(",", ": "))]
        lines = ["["]
        for index, item in enumerate(value):
            rendered = _render_weight_json(item, level + 1)
            lines.append(f"{'  ' * (level + 1)}{rendered[0].lstrip()}")
            lines.extend(rendered[1:])
            if index < len(value) - 1:
                lines[-1] += ","
        lines.append(f"{indent}]")
        return lines
    return [json.dumps(value, ensure_ascii=True)]


def save_weights(path: Path, model: Any) -> None:
    """Write finite JSON weights once; never execute or overwrite artifacts."""
    payload = weights_payload(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.write_text("\n".join(_render_weight_json(payload)) + "\n", encoding="utf-8")


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
        try:
            tensor = torch.tensor(values, dtype=reference.dtype)
        except (TypeError, ValueError, RuntimeError) as error:
            raise ValueError("weight values are invalid") from error
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


def build_preference_pairs(
    manifest: DatasetManifest, split: str = "train"
) -> tuple[PreferencePair, ...]:
    """Build same-state pairs whose actions pass safety but differ in quality."""
    from .replay import initial_state, run_replay

    pairs: list[PreferencePair] = []
    rejected_actions = tuple(action for action in ALL_ACTIONS if action != "ASK_INPUT")
    for case in manifest.cases:
        if case.split != split:
            continue
        state = initial_state(case)
        chosen_action = deterministic_policy(state)
        chosen_result = run_replay(case, deterministic_policy)
        if not chosen_result.safety_passed:
            continue
        for rejected_action in rejected_actions:
            if rejected_action == chosen_action:
                continue

            def fixed_policy(_state: ReplayState, value: str = rejected_action) -> str:
                return value

            rejected_result = run_replay(case, fixed_policy)
            if not rejected_result.safety_passed:
                continue
            if chosen_result.score <= rejected_result.score:
                continue
            pairs.append(
                (
                    state_features(state),
                    ACTION_INDEX[chosen_action],
                    ACTION_INDEX[rejected_action],
                )
            )
    if not pairs:
        raise ValueError(f"no safe preference pairs are available for {split}")
    return tuple(pairs)


def dpo_loss(
    policy_model: Any,
    reference_model: Any,
    features: Any,
    chosen: Any,
    rejected: Any,
    *,
    beta: float = 0.1,
    reference_log: Any | None = None,
) -> Any:
    """Return the standard DPO pairwise loss for a frozen reference policy."""
    if not math.isfinite(beta) or beta <= 0.0:
        raise ValueError("DPO beta must be finite and positive")
    torch, _ = _torch()
    policy_log = torch.log_softmax(policy_model(features), dim=-1)
    if reference_log is None:
        with torch.no_grad():
            reference_log = torch.log_softmax(reference_model(features), dim=-1)
    policy_diff = policy_log.gather(1, chosen[:, None]).squeeze(1) - policy_log.gather(
        1, rejected[:, None]
    ).squeeze(1)
    reference_diff = reference_log.gather(1, chosen[:, None]).squeeze(1) - reference_log.gather(
        1, rejected[:, None]
    ).squeeze(1)
    loss = (-torch.nn.functional.logsigmoid(beta * (policy_diff - reference_diff))).mean()
    if not bool(torch.isfinite(loss)):
        raise ValueError("DPO produced a non-finite loss")
    return loss


def _artifact(
    method: str,
    seed: int,
    manifest: DatasetManifest,
    initial: Any,
    model: Any,
    metrics: dict[str, float],
    config: dict[str, int | float | str],
    weight_path: str,
    reference_weight_sha256: str | None = None,
    checkpoint_metrics: tuple[dict[str, float], ...] = (),
) -> TrainingArtifact:
    return TrainingArtifact(
        artifact_id=f"phase12-train-{method}-seed-{seed}",
        method=method,  # type: ignore[arg-type]
        code_version=current_code_version(),
        dataset_id=manifest.dataset_id,
        dataset_digest=manifest.dataset_digest,
        seed=seed,
        feature_version=FEATURE_VERSION,
        action_version=ACTION_VERSION,
        config=config,
        initial_weight_sha256=weights_digest(initial),
        weight_sha256=weights_digest(model),
        reference_weight_sha256=reference_weight_sha256,
        metrics=metrics,
        checkpoint_metrics=checkpoint_metrics,
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
    time_limit_seconds: float = MAX_TRAIN_SECONDS,
    weight_path: Path | None = None,
) -> TrainResult:
    """Train the fixed MLP on safe action labels with dev early stopping."""
    if max_epochs < 1 or patience < 1:
        raise ValueError("training bounds must be positive")
    time_limit_seconds = _validate_time_limit(time_limit_seconds)
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
    started = time.perf_counter()
    for _ in range(max_epochs):
        if time.perf_counter() - started > time_limit_seconds:
            raise TimeoutError("SFT seed exceeded 120 second budget")
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(x_train), y_train)
        if not bool(torch.isfinite(loss)):
            raise ValueError("SFT produced a non-finite loss")
        loss.backward()
        _check_finite_gradients(model)
        optimizer.step()
        _check_finite_parameters(model)
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
    time_limit_seconds: float = MAX_TRAIN_SECONDS,
    weight_path: Path | None = None,
) -> TrainResult:
    """Apply the standard pairwise DPO objective with a frozen reference."""
    if max_epochs < 1 or patience < 1:
        raise ValueError("training bounds must be positive")
    time_limit_seconds = _validate_time_limit(time_limit_seconds)
    torch, _ = _torch()
    torch.manual_seed(seed)
    model = copy.deepcopy(base_model)
    initial = copy.deepcopy(model)
    reference = copy.deepcopy(base_model)
    for parameter in reference.parameters():
        parameter.requires_grad_(False)
    train_pairs = build_preference_pairs(manifest, "train")
    dev_pairs = build_preference_pairs(manifest, "dev")

    def tensors(
        pairs: tuple[PreferencePair, ...],
    ) -> tuple[Any, Any, Any]:
        return (
            torch.tensor([item[0] for item in pairs], dtype=torch.float32),
            torch.tensor([item[1] for item in pairs], dtype=torch.long),
            torch.tensor([item[2] for item in pairs], dtype=torch.long),
        )

    train_features, train_chosen, train_rejected = tensors(train_pairs)
    dev_features, dev_chosen, dev_rejected = tensors(dev_pairs)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    history: list[float] = []
    best_state = copy.deepcopy(model.state_dict())
    best_loss = float("inf")
    stale = 0
    started = time.perf_counter()
    with torch.no_grad():
        train_ref_log = torch.log_softmax(reference(train_features), dim=-1)
        dev_ref_log = torch.log_softmax(reference(dev_features), dim=-1)

    for _ in range(max_epochs):
        if time.perf_counter() - started > time_limit_seconds:
            raise TimeoutError("DPO seed exceeded 120 second budget")
        optimizer.zero_grad()
        loss = dpo_loss(
            model,
            reference,
            train_features,
            train_chosen,
            train_rejected,
            beta=0.1,
            reference_log=train_ref_log,
        )
        if not bool(torch.isfinite(loss)):
            raise ValueError("DPO produced a non-finite loss")
        loss.backward()
        _check_finite_gradients(model)
        optimizer.step()
        _check_finite_parameters(model)
        with torch.no_grad():
            dev_loss = dpo_loss(
                model,
                reference,
                dev_features,
                dev_chosen,
                dev_rejected,
                beta=0.1,
                reference_log=dev_ref_log,
            )
        value = float(dev_loss.detach().item())
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
    if weights_digest(reference) != weights_digest(base_model):
        raise ValueError("DPO reference parameters changed")
    metrics = {
        "final_dpo_loss": best_loss,
        "preference_pairs": float(len(train_pairs)),
        "dev_preference_pairs": float(len(dev_pairs)),
    }
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
            reference_weight_sha256=weights_digest(reference),
        ),
        tuple(history),
    )
    return _write_result(result, weight_path)


def train_reinforce(
    manifest: DatasetManifest,
    base_model: Any,
    *,
    seed: int = 17,
    episodes: int = 300,
    time_limit_seconds: float = MAX_TRAIN_SECONDS,
    weight_path: Path | None = None,
) -> TrainResult:
    """Run a bounded REINFORCE loop in the local procurement environment."""
    if episodes < 1 or episodes > 300:
        raise ValueError("REINFORCE episode bound must be between one and 300")
    time_limit_seconds = _validate_time_limit(time_limit_seconds)
    torch, _ = _torch()
    from .rl import ProcurementEnv, safe_reward_config

    torch.manual_seed(seed)
    model = copy.deepcopy(base_model)
    initial = copy.deepcopy(model)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    cases = tuple(case for case in manifest.cases if case.split == "train")
    if not cases:
        raise ValueError("no train cases are available")
    baseline = 0.0
    started = time.perf_counter()
    returns: list[float] = []
    episode_steps: list[int] = []
    successes = 0
    invalid_actions = 0
    best_state: dict[str, Any] | None = None
    best_key: tuple[bool, float, float, float] | None = None
    checkpoint_metrics: list[dict[str, float]] = []
    selected_episode = 0
    for episode in range(episodes):
        if time.perf_counter() - started > time_limit_seconds:
            raise TimeoutError("REINFORCE seed exceeded 120 second budget")
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
        episode_steps.append(len(rewards))
        previous_baseline = baseline
        if log_probs:
            advantages = torch.tensor(
                [value - previous_baseline for value in discounted], dtype=torch.float32
            )
            loss = -(torch.stack(log_probs) * advantages).sum()
            if not bool(torch.isfinite(loss)):
                raise ValueError("REINFORCE produced a non-finite loss")
            optimizer.zero_grad()
            _check_finite_gradients(model)
            loss.backward()
            _check_finite_gradients(model)
            optimizer.step()
            _check_finite_parameters(model)
        baseline = 0.9 * baseline + 0.1 * episode_return
        successes += int(safe and final_status == case.expected_status)
        completed_episode = episode + 1
        if completed_episode % 25 == 0 or completed_episode == episodes:
            dev_metrics, _ = evaluate_policy_model(manifest, model, "dev")
            checkpoint = {
                "episode": float(completed_episode),
                "dev_success_rate": dev_metrics["success_rate"],
                "dev_safety_rate": dev_metrics["safety_rate"],
                "dev_average_steps": dev_metrics["average_steps"],
            }
            checkpoint_metrics.append(checkpoint)
            key = (
                dev_metrics["safety_rate"] >= 1.0,
                dev_metrics["success_rate"],
                -dev_metrics["average_steps"],
                -float(completed_episode),
            )
            if best_key is None or key > best_key:
                best_key = key
                best_state = copy.deepcopy(model.state_dict())
                selected_episode = completed_episode
    if best_state is None:
        raise ValueError("REINFORCE produced no dev checkpoint")
    model.load_state_dict(best_state)
    selected_metrics = next(
        item for item in checkpoint_metrics if item["episode"] == float(selected_episode)
    )
    metrics = {
        "episodes": float(episodes),
        "mean_return": sum(returns) / len(returns),
        "success_rate": successes / episodes,
        "invalid_actions": float(invalid_actions),
        "average_steps": sum(episode_steps) / len(episode_steps),
        "selected_episode": float(selected_episode),
        "selected_dev_success_rate": selected_metrics["dev_success_rate"],
        "selected_dev_safety_rate": selected_metrics["dev_safety_rate"],
        "selected_dev_average_steps": selected_metrics["dev_average_steps"],
        "dev_checkpoint_count": float(len(checkpoint_metrics)),
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
            checkpoint_metrics=tuple(checkpoint_metrics),
        ),
        tuple(returns),
    )
    return _write_result(result, weight_path)
