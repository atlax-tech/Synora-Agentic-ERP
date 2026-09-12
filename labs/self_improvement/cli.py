"""Command line entry points for the Phase 12 offline lab."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from .artifacts import (
    PHASE12_RELATIVE_ROOT,
    code_version,
    read_manifest,
    read_records,
    verify_manifest,
    verify_records,
    write_manifest,
    write_records,
)
from .candidates import (
    build_selection,
    make_prompt_candidate,
    make_skill_candidate,
    read_candidate,
    rollback_selection,
    write_candidate,
    write_selection,
)
from .contracts import DatasetManifest, ExperimentRecord
from .data import audit_historical_failures, build_synthetic_manifest
from .evaluation import (
    CallBudget,
    best_of_n_replay,
    evaluate_replay_cases,
    reflection_replay,
    run_live_baseline,
)
from .replay import deterministic_policy
from .training import load_weights, train_dpo, train_reinforce, train_sft


def _root(value: str) -> Path:
    return Path(value).resolve()


def _manifest(root: Path) -> DatasetManifest:
    return read_manifest(root, f"{PHASE12_RELATIVE_ROOT}/dataset-phase12-synthetic-v1.json")


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2))


def _fixed_action(action: str) -> Callable[[object], str]:
    def choose(_state: object) -> str:
        return action

    return choose


def _write_audit(root: Path) -> dict[str, object]:
    records = audit_historical_failures(root)
    payload = {
        "schema_version": "1",
        "code_version": code_version(),
        "records": [record.model_dump(mode="json") for record in records],
    }
    path = root / PHASE12_RELATIVE_ROOT / "audited-failures.json"
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return {"path": str(path), "records": len(records)}


def _replay_records(
    manifest: DatasetManifest, split: str, method: str, version: str
) -> tuple[ExperimentRecord, ...]:
    cases = tuple(case for case in manifest.cases if case.split == split)
    records = []
    for case in cases:
        if method == "baseline":
            result = evaluate_replay_cases(
                (case,),
                deterministic_policy,
                code_version=version,
                method=method,
                dataset_digest=manifest.dataset_digest,
            )[0]
        elif method == "reflection":
            outcome = reflection_replay(case, lambda _state: "FINISH", deterministic_policy)
            result = evaluate_replay_cases(
                (case,),
                _fixed_action(
                    outcome.result.action_sequence[0]
                    if outcome.result.action_sequence
                    else "FINISH"
                ),
                code_version=version,
                method=method,
                dataset_digest=manifest.dataset_digest,
            )[0].model_copy(
                update={
                    "calls": outcome.calls,
                    "status": "SUCCEEDED" if outcome.result.verifier_passed else "REJECTED",
                    "verifier_passed": outcome.result.verifier_passed,
                    "safety_passed": outcome.result.safety_passed,
                    "score": outcome.result.score,
                    "failure_code": None
                    if outcome.result.verifier_passed
                    else outcome.result.failure_code,
                }
            )
        elif method == "best-of-3":
            outcome = best_of_n_replay(case, (lambda _state: "FINISH", deterministic_policy), n=2)
            result = evaluate_replay_cases(
                (case,),
                _fixed_action(
                    outcome.result.action_sequence[0]
                    if outcome.result.action_sequence
                    else "FINISH"
                ),
                code_version=version,
                method=method,
                dataset_digest=manifest.dataset_digest,
            )[0].model_copy(
                update={
                    "calls": outcome.calls,
                    "status": "SUCCEEDED" if outcome.result.verifier_passed else "REJECTED",
                    "verifier_passed": outcome.result.verifier_passed,
                    "safety_passed": outcome.result.safety_passed,
                    "score": outcome.result.score,
                    "failure_code": None
                    if outcome.result.verifier_passed
                    else outcome.result.failure_code,
                }
            )
        else:
            raise ValueError("unsupported replay method")
        records.append(result)
    return tuple(records)


def _existing_calls(root: Path) -> int:
    total = 0
    output = root / PHASE12_RELATIVE_ROOT
    if not output.exists():
        return 0
    for path in output.glob("*.jsonl"):
        total += sum(record.calls for record in read_records(root, str(path.relative_to(root))))
    return total


def _cmd_evaluate(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    if args.engine == "replay":
        records = _replay_records(manifest, args.split, args.method, code_version())
    else:
        from agent_runtime.providers import ProviderError, provider_for_role

        try:
            provider = provider_for_role("assist")
        except ProviderError as error:
            raise RuntimeError(f"live provider unavailable: {error.failure_code}") from error
        budget = CallBudget(maximum=1_200, used=_existing_calls(args.root))
        cases = tuple(case for case in manifest.cases if case.split == args.split)
        records = tuple(
            run_live_baseline(
                case,
                provider,
                budget,
                code_version=code_version(),
                model=getattr(provider, "model", "assist"),
                repeat=repeat,
                dataset_id=manifest.dataset_id,
                dataset_digest=manifest.dataset_digest,
            )
            for repeat in range(1, args.repeats + 1)
            for case in cases
        )
    path = write_records(
        args.root, f"evaluation-{args.engine}-{args.method}-{args.split}.jsonl", records
    )
    return {
        "path": str(path),
        "records": len(records),
        "calls": sum(record.calls for record in records),
    }


def _cmd_train(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    path = args.root / PHASE12_RELATIVE_ROOT / f"weights-{args.method}-{args.seed}.json"
    if args.method == "sft":
        result = train_sft(manifest, seed=args.seed, weight_path=path)
    elif args.method == "dpo":
        source = args.root / PHASE12_RELATIVE_ROOT / f"weights-sft-{args.seed}.json"
        result = train_dpo(load_weights(source), manifest, seed=args.seed, weight_path=path)
    else:
        result = train_reinforce(manifest, seed=args.seed, weight_path=path)
    metadata = result.artifact.model_dump(mode="json")
    metadata_path = path.with_suffix(".metadata.json")
    if metadata_path.exists():
        raise FileExistsError(metadata_path)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return {"weight_path": str(path), "metadata_path": str(metadata_path), "artifact": metadata}


def _cmd_verify(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    output = args.root / PHASE12_RELATIVE_ROOT
    record_files = sorted(output.glob("*.jsonl"))
    record_count = 0
    for path in record_files:
        records = read_records(args.root, str(path.relative_to(args.root)))
        verify_records(records, manifest)
        record_count += len(records)
    candidate_count = (
        len(list((output / "candidates").glob("*.json"))) if (output / "candidates").exists() else 0
    )
    selection_count = (
        len(list((output / "selections").glob("*.json"))) if (output / "selections").exists() else 0
    )
    weight_count = 0
    for path in output.glob("weights-*.json"):
        if path.name.endswith(".metadata.json"):
            continue
        load_weights(path)
        weight_count += 1
    return {
        "manifest": manifest.dataset_digest,
        "record_files": len(record_files),
        "records": record_count,
        "candidates": candidate_count,
        "selections": selection_count,
        "weights": weight_count,
        "status": "PASS",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m labs.self_improvement.cli")
    parser.add_argument("--root", default=".", type=_root)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit-data")
    sub.add_parser("prepare-data")
    candidates = sub.add_parser("make-candidates")
    candidates.add_argument("--source", default="phase12-synthetic")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--engine", choices=("replay", "live"), default="replay")
    evaluate.add_argument("--split", choices=("train", "dev", "test"), default="dev")
    evaluate.add_argument(
        "--method", choices=("baseline", "reflection", "best-of-3"), default="baseline"
    )
    evaluate.add_argument("--repeats", type=int, default=1)
    select = sub.add_parser("select-lab")
    select.add_argument("--candidate-id", required=True)
    select.add_argument("--previous-id", default="native-agent/A")
    select.add_argument("--evidence", nargs="+", required=True)
    select.add_argument("--reason", required=True)
    rollback = sub.add_parser("rollback-lab")
    rollback.add_argument("--selection-id", required=True)
    rollback.add_argument("--evidence", nargs="+", required=True)
    rollback.add_argument("--reason", required=True)
    train = sub.add_parser("train")
    train.add_argument("--method", choices=("sft", "dpo", "reinforce"), required=True)
    train.add_argument("--seed", type=int, choices=(17, 29, 43), default=17)
    sub.add_parser("verify-artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit-data":
            result = _write_audit(args.root)
        elif args.command == "prepare-data":
            manifest = build_synthetic_manifest(code_version())
            result = {
                "path": str(write_manifest(args.root, manifest)),
                "dataset": manifest.model_dump(mode="json"),
            }
        elif args.command == "make-candidates":
            source = (args.source,)
            prompt = make_prompt_candidate(
                source, "Verify the highest-impact unresolved read-only fact before finishing."
            )
            skill = make_skill_candidate(
                source,
                "When evidence is incomplete, request the missing fact and preserve unknowns.",
            )
            result = {
                "prompt": str(write_candidate(args.root / PHASE12_RELATIVE_ROOT, prompt)),
                "skill": str(write_candidate(args.root / PHASE12_RELATIVE_ROOT, skill)),
            }
        elif args.command == "evaluate":
            result = _cmd_evaluate(args)
        elif args.command == "select-lab":
            candidate = read_candidate(args.root / PHASE12_RELATIVE_ROOT, args.candidate_id)
            selection = build_selection(
                args.previous_id, candidate.candidate_id, args.evidence, args.reason
            )
            result = {
                "path": str(write_selection(args.root / PHASE12_RELATIVE_ROOT, selection)),
                "selection": selection.model_dump(mode="json"),
            }
        elif args.command == "rollback-lab":
            selection, path = rollback_selection(
                args.root / PHASE12_RELATIVE_ROOT, args.selection_id, args.evidence, args.reason
            )
            result = {"path": str(path), "selection": selection.model_dump(mode="json")}
        elif args.command == "train":
            result = _cmd_train(args)
        else:
            result = _cmd_verify(args)
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError, OSError) as error:
        print(f"phase12: {error}", file=sys.stderr)
        return 2
    _json_print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
