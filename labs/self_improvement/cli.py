"""Command line entry points for the Phase 12 offline lab."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import (
    PHASE12_RELATIVE_ROOT,
    code_version,
    read_json,
    read_manifest,
    read_records,
    verify_manifest,
    verify_records,
    write_json_once,
    write_manifest,
    write_records,
)
from .candidates import (
    build_selection,
    make_prompt_candidate,
    make_skill_candidate,
    read_candidate,
    read_selection,
    rollback_selection,
    version_content_digest,
    write_candidate,
    write_selection,
)
from .contracts import (
    CandidateVersion,
    DatasetCase,
    DatasetManifest,
    ExperimentRecord,
    ReviewedCase,
    TrainingArtifact,
)
from .data import audit_historical_failures, build_synthetic_manifest
from .evaluation import (
    CallBudget,
    best_of_n_replay,
    evaluate_replay_cases,
    reflection_replay,
    run_live_baselines,
)
from .replay import ReplayResult, candidate_policy, deterministic_policy
from .reporting import write_reports
from .training import load_weights, train_dpo, train_reinforce, train_sft, weights_digest


def _root(value: str) -> Path:
    return Path(value).resolve()


def _manifest(root: Path) -> DatasetManifest:
    return read_manifest(root, f"{PHASE12_RELATIVE_ROOT}/dataset-phase12-synthetic-v1.json")


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2))


def _write_audit(root: Path) -> dict[str, object]:
    records = audit_historical_failures(root)
    payload = {
        "schema_version": "1",
        "code_version": code_version(),
        "records": [record.model_dump(mode="json") for record in records],
    }
    path = write_json_once(root, f"{PHASE12_RELATIVE_ROOT}/audited-failures.json", payload)
    return {"path": str(path), "records": len(records)}


def _read_verified_audit(root: Path) -> tuple[ReviewedCase, ...]:
    payload = read_json(root, f"{PHASE12_RELATIVE_ROOT}/audited-failures.json")
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("audited failure artifact has an invalid shape")
    reviewed = tuple(ReviewedCase.model_validate(item) for item in payload["records"])
    current = audit_historical_failures(root)
    if tuple(record.model_dump(mode="json") for record in reviewed) != tuple(
        record.model_dump(mode="json") for record in current
    ):
        raise ValueError("audited failure artifact does not match its allowlisted sources")
    return reviewed


def _candidate_sources(root: Path) -> tuple[str, ...]:
    reviewed = _read_verified_audit(root)
    sources = tuple(
        record.case_id
        for record in reviewed
        if record.review_status in {"ACCEPTED", "BACKGROUND_ONLY"}
    )
    if not sources:
        raise RuntimeError("no reviewed historical failure is available for candidates")
    return sources[:10]


def _replay_record(
    case: DatasetCase,
    result: ReplayResult,
    *,
    method: str,
    version: str,
    repeat: int,
    calls: int,
    dataset: DatasetManifest,
) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=f"phase12-exp-replay-{method.lower()}-{repeat}-{case.case_id}",
        code_version=version,
        dataset_id=dataset.dataset_id,
        dataset_digest=dataset.dataset_digest,
        split=case.split,
        method=method,
        model="deterministic-replay",
        repeat=repeat,
        status="SUCCEEDED" if result.verifier_passed else "REJECTED",
        output_action=result.action_sequence[-1] if result.action_sequence else None,
        verifier_passed=result.verifier_passed,
        safety_passed=result.safety_passed,
        score=result.score,
        failure_code=None if result.verifier_passed else result.failure_code,
        elapsed_ms=0.0,
        calls=calls,
    )


def _replay_records(
    manifest: DatasetManifest,
    split: str,
    method: str,
    version: str,
    repeats: int = 1,
    candidate_id: str | None = None,
    candidate_content: str | None = None,
) -> tuple[ExperimentRecord, ...]:
    if repeats < 1 or repeats > 3:
        raise ValueError("replay repeats must be between one and three")
    cases = tuple(case for case in manifest.cases if case.split == split)
    records = []
    for repeat in range(1, repeats + 1):
        for case in cases:
            if method in {"baseline", "prompt-candidate", "skill-candidate"}:
                policy = (
                    candidate_policy(candidate_content)
                    if candidate_content is not None
                    else deterministic_policy
                )
                result = evaluate_replay_cases(
                    (case,),
                    policy,
                    code_version=version,
                    method=method,
                    dataset_id=manifest.dataset_id,
                    dataset_digest=manifest.dataset_digest,
                    repeat=repeat,
                    candidate_id=candidate_id,
                )[0]
                records.append(result)
                continue
            if method == "reflection":
                outcome = reflection_replay(case, lambda _state: "FINISH", deterministic_policy)
            elif method == "best-of-3":
                outcome = best_of_n_replay(
                    case,
                    (lambda _state: "FINISH", deterministic_policy, deterministic_policy),
                    n=3,
                )
            else:
                raise ValueError("unsupported replay method")
            records.append(
                _replay_record(
                    case,
                    outcome.result,
                    method=method,
                    version=version,
                    repeat=repeat,
                    calls=outcome.calls,
                    dataset=manifest,
                )
            )
    return tuple(records)


def _candidate_for_method(root: Path, method: str) -> CandidateVersion:
    expected_kind = "PROMPT" if method == "prompt-candidate" else "SKILL"
    candidate_dir = root / PHASE12_RELATIVE_ROOT / "candidates"
    paths = sorted(candidate_dir.glob("*.json")) if candidate_dir.exists() else []
    matching = tuple(
        read_candidate(root / PHASE12_RELATIVE_ROOT, path.stem)
        for path in paths
        if path.stem.startswith("phase12-prompt-") or path.stem.startswith("phase12-skill-")
    )
    matching = tuple(candidate for candidate in matching if candidate.kind == expected_kind)
    if len(matching) == 1:
        return matching[0]
    if not matching:
        raise FileNotFoundError(f"no {expected_kind} candidate artifact is available")
    raise RuntimeError(f"{method} requires one frozen candidate artifact; found {len(matching)}")


def _existing_calls(root: Path) -> int:
    total = 0
    output = root / PHASE12_RELATIVE_ROOT
    if not output.exists():
        return 0
    for path in output.glob("*.jsonl"):
        total += sum(record.calls for record in read_records(root, str(path.relative_to(root))))
    return total


def _validate_lab_version(root: Path, version_id: str) -> None:
    if version_id in {"native-agent/A", "skill-registry/v1"}:
        return
    if version_id.startswith("phase12-"):
        read_candidate(root / PHASE12_RELATIVE_ROOT, version_id)
        return
    raise ValueError("unknown lab version")


def _validate_evidence(root: Path, evidence_ids: tuple[str, ...]) -> None:
    paths = sorted((root / PHASE12_RELATIVE_ROOT).glob("*.jsonl"))
    if not paths:
        return
    known: set[str] = set()
    for path in paths:
        relative = str(path.relative_to(root))
        known.update(record.experiment_id for record in read_records(root, relative))
    missing = sorted(set(evidence_ids) - known)
    if missing:
        raise FileNotFoundError(f"evaluation evidence is missing: {', '.join(missing)}")


def _cmd_evaluate(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    if args.repeats < 1 or args.repeats > 3:
        raise ValueError("evaluation repeats must be between one and three")
    if args.engine == "replay":
        candidate_id = None
        candidate_content = None
        if args.method in {"prompt-candidate", "skill-candidate"}:
            candidate = _candidate_for_method(args.root, args.method)
            candidate_id = candidate.candidate_id
            candidate_content = candidate.content
        records = _replay_records(
            manifest,
            args.split,
            args.method,
            code_version(),
            repeats=args.repeats,
            candidate_id=candidate_id,
            candidate_content=candidate_content,
        )
    else:
        if args.method != "baseline":
            raise ValueError("live engine currently supports only the baseline method")
        from agent_runtime.providers import ProviderError, provider_for_role

        try:
            provider = provider_for_role("assist")
        except ProviderError as error:
            raise RuntimeError(f"live provider unavailable: {error.failure_code}") from error
        budget = CallBudget(maximum=1_200, used=_existing_calls(args.root))
        cases = tuple(case for case in manifest.cases if case.split == args.split)
        records = run_live_baselines(
            cases,
            provider,
            budget,
            code_version=code_version(),
            model=getattr(provider, "model", "assist"),
            repeats=args.repeats,
            dataset_id=manifest.dataset_id,
            dataset_digest=manifest.dataset_digest,
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
        source = args.root / PHASE12_RELATIVE_ROOT / f"weights-sft-{args.seed}.json"
        result = train_reinforce(manifest, load_weights(source), seed=args.seed, weight_path=path)
    metadata = result.artifact.model_dump(mode="json")
    metadata_path = write_json_once(
        args.root,
        f"{PHASE12_RELATIVE_ROOT}/{path.with_suffix('.metadata.json').name}",
        metadata,
    )
    return {"weight_path": str(path), "metadata_path": str(metadata_path), "artifact": metadata}


def _cmd_verify(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    reviewed_ids = {record.case_id for record in _read_verified_audit(args.root)}
    output = args.root / PHASE12_RELATIVE_ROOT
    candidate_dir = output / "candidates"
    candidate_paths = sorted(candidate_dir.glob("*.json")) if candidate_dir.exists() else []
    candidates_by_id: dict[str, CandidateVersion] = {}
    for path in candidate_paths:
        if path.is_symlink():
            raise ValueError("candidate artifact cannot be a symlink")
        candidate = read_candidate(output, path.stem)
        if candidate.candidate_id != path.stem:
            raise ValueError("candidate filename does not match its ID")
        unknown_sources = sorted(set(candidate.source_case_ids) - reviewed_ids)
        if unknown_sources:
            raise ValueError(
                "candidate references unaudited historical failures: " + ", ".join(unknown_sources)
            )
        candidates_by_id[candidate.candidate_id] = candidate
    record_files = sorted(output.glob("*.jsonl"))
    record_count = 0
    all_records: list[ExperimentRecord] = []
    for path in record_files:
        records = read_records(args.root, str(path.relative_to(args.root)))
        verify_records(records, manifest)
        all_records.extend(records)
        record_count += len(records)
        for record in records:
            if record.method not in {"prompt-candidate", "skill-candidate"}:
                continue
            if record.candidate_id is None:
                raise ValueError("candidate experiment is missing candidate_id")
            record_candidate = candidates_by_id.get(record.candidate_id)
            expected_kind = "PROMPT" if record.method == "prompt-candidate" else "SKILL"
            if record_candidate is None or record_candidate.kind != expected_kind:
                raise ValueError("candidate experiment references the wrong artifact")
    verify_records(all_records, manifest)
    selection_dir = output / "selections"
    selection_paths = sorted(selection_dir.glob("*.json")) if selection_dir.exists() else []
    candidate_ids = {path.stem for path in candidate_paths}
    for path in selection_paths:
        if path.is_symlink():
            raise ValueError("selection artifact cannot be a symlink")
        selection = read_selection(output, path.stem)
        if selection.selection_id != path.stem:
            raise ValueError("selection filename does not match its ID")
        for version_id in (selection.previous_id, selection.selected_id):
            if version_id.startswith("phase12-") and version_id not in candidate_ids:
                raise FileNotFoundError(f"selection references missing candidate: {version_id}")
        for version_id, expected_digest in selection.version_digests.items():
            if version_content_digest(output, version_id) != expected_digest:
                raise ValueError(f"selection version digest mismatch: {version_id}")
    weight_count = 0
    for path in output.glob("weights-*.json"):
        if path.name.endswith(".metadata.json"):
            continue
        if path.is_symlink():
            raise ValueError("weight artifact cannot be a symlink")
        model = load_weights(path)
        metadata_path = path.with_suffix(".metadata.json")
        if not metadata_path.is_file() or metadata_path.is_symlink():
            raise FileNotFoundError(f"missing training metadata: {metadata_path.name}")
        metadata = TrainingArtifact.model_validate_json(metadata_path.read_text(encoding="utf-8"))
        expected_path = str(path.relative_to(args.root))
        if metadata.weight_path != expected_path:
            raise ValueError("training metadata path does not match weight artifact")
        if (
            metadata.dataset_id != manifest.dataset_id
            or metadata.dataset_digest != manifest.dataset_digest
        ):
            raise ValueError("training artifact is bound to a different dataset")
        if metadata.weight_sha256 != weights_digest(model):
            raise ValueError("training weight digest mismatch")
        weight_count += 1
    return {
        "manifest": manifest.dataset_digest,
        "record_files": len(record_files),
        "records": record_count,
        "candidates": len(candidate_paths),
        "selections": len(selection_paths),
        "weights": weight_count,
        "status": "PASS",
    }


def _cmd_report(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    output = args.root / PHASE12_RELATIVE_ROOT
    records: list[ExperimentRecord] = []
    for path in sorted(output.glob("*.jsonl")):
        records.extend(read_records(args.root, str(path.relative_to(args.root))))
    verify_records(records, manifest)
    training_artifacts = [
        TrainingArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(output.glob("weights-*.metadata.json"))
        if path.is_file() and not path.is_symlink()
    ]
    for artifact in training_artifacts:
        if (
            artifact.dataset_id != manifest.dataset_id
            or artifact.dataset_digest != manifest.dataset_digest
        ):
            raise ValueError("training artifact is bound to a different dataset")
    report_paths = write_reports(
        args.root,
        manifest,
        records,
        training_artifacts,
        code_version=code_version(),
    )
    return {key: value for key, value in report_paths.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m labs.self_improvement.cli")
    parser.add_argument("--root", default=".", type=_root)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit-data")
    sub.add_parser("prepare-data")
    sub.add_parser("make-candidates")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--engine", choices=("replay", "live"), default="replay")
    evaluate.add_argument("--split", choices=("train", "dev", "test"), default="dev")
    evaluate.add_argument(
        "--method",
        choices=("baseline", "reflection", "best-of-3", "prompt-candidate", "skill-candidate"),
        default="baseline",
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
    sub.add_parser("report")
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
            source = _candidate_sources(args.root)
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
            _validate_lab_version(args.root, args.previous_id)
            if candidate.parent_id != args.previous_id:
                raise ValueError("candidate parent does not match the selected previous version")
            _validate_evidence(args.root, tuple(args.evidence))
            selection = build_selection(
                args.previous_id,
                candidate.candidate_id,
                args.evidence,
                args.reason,
                version_digests={
                    args.previous_id: version_content_digest(
                        args.root / PHASE12_RELATIVE_ROOT, args.previous_id
                    ),
                    candidate.candidate_id: candidate.content_sha256,
                },
            )
            result = {
                "path": str(write_selection(args.root / PHASE12_RELATIVE_ROOT, selection)),
                "selection": selection.model_dump(mode="json"),
            }
        elif args.command == "rollback-lab":
            _validate_evidence(args.root, tuple(args.evidence))
            selection, path = rollback_selection(
                args.root / PHASE12_RELATIVE_ROOT, args.selection_id, args.evidence, args.reason
            )
            result = {"path": str(path), "selection": selection.model_dump(mode="json")}
        elif args.command == "train":
            result = _cmd_train(args)
        elif args.command == "report":
            result = _cmd_report(args)
        else:
            result = _cmd_verify(args)
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError, OSError) as error:
        print(f"phase12: {error}", file=sys.stderr)
        return 2
    _json_print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
