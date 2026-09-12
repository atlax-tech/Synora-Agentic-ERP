"""Command line entry points for the Phase 12 offline lab."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .artifacts import (
    PHASE12_RELATIVE_ROOT,
    append_record,
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
    apply_selection,
    build_selection,
    make_prompt_candidate,
    make_skill_candidate,
    read_active_version,
    read_candidate,
    read_selection,
    rollback_selection,
    version_content_digest,
    write_candidate,
)
from .contracts import (
    CandidateVersion,
    DatasetCase,
    DatasetManifest,
    ExperimentPlan,
    ExperimentRecord,
    ReviewedCase,
    TrainingArtifact,
    canonical_json,
    digest_json,
)
from .data import audit_historical_failures, build_synthetic_manifest
from .evaluation import (
    RESERVATION_LEDGER_NAME,
    CallBudget,
    ReservationLedger,
    best_of_n_replay,
    evaluate_replay_cases,
    reflection_replay,
    run_live_methods,
)
from .replay import ReplayResult, candidate_policy, deterministic_policy
from .reporting import build_summary, write_reports
from .training import load_weights, train_dpo, train_reinforce, train_sft, weights_digest
from .weight_evaluation import evaluate_weight_artifact


def _root(value: str) -> Path:
    return Path(value).resolve()


def _manifest(root: Path) -> DatasetManifest:
    return read_manifest(root, f"{PHASE12_RELATIVE_ROOT}/dataset-phase12-synthetic-v2.json")


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
        case_id=case.case_id,
        group_id=case.group_id,
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
    candidate_content_sha256: str | None = None,
    candidate_boundary_sha256: str | None = None,
    case_id: str | None = None,
) -> tuple[ExperimentRecord, ...]:
    if repeats < 1 or repeats > 3:
        raise ValueError("replay repeats must be between one and three")
    cases = tuple(case for case in manifest.cases if case.split == split)
    if case_id is not None:
        cases = tuple(case for case in cases if case.case_id == case_id)
        if not cases:
            raise ValueError("case id is missing from the requested split")
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
                    candidate_content_sha256=candidate_content_sha256,
                    candidate_boundary_sha256=candidate_boundary_sha256,
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
        if path.name == RESERVATION_LEDGER_NAME:
            continue
        total += sum(record.calls for record in read_records(root, str(path.relative_to(root))))
    return total


def _reservation_key_for_record(record: ExperimentRecord) -> str | None:
    """Return the exact persisted reservation key for a live record.

    Legacy records created before reservation keys were persisted are deliberately
    excluded: deriving a suffix would risk consuming another batch's reservation.
    """
    if not record.experiment_id.startswith("phase12-exp-live-"):
        return None
    return record.reservation_key


def _reservation_keys_for_record(record: ExperimentRecord) -> tuple[str, ...]:
    if not record.experiment_id.startswith("phase12-exp-live-"):
        return ()
    if record.reservation_keys:
        return record.reservation_keys
    return (record.reservation_key,) if record.reservation_key is not None else ()


def _live_record_keys(root: Path) -> tuple[str, ...]:
    output = root / PHASE12_RELATIVE_ROOT
    if not output.exists():
        return ()
    keys: list[str] = []
    for path in output.glob("*.jsonl"):
        if path.name == RESERVATION_LEDGER_NAME:
            continue
        for record in read_records(root, str(path.relative_to(root))):
            if record.calls > 0:
                keys.extend(_reservation_keys_for_record(record))
    return tuple(keys)


def _validated_batch_id(value: str | None) -> str:
    if value is None or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", value):
        raise ValueError("live evaluation requires a lowercase explicit batch id")
    return value


_FROZEN_REPLAY_COVERAGE: tuple[tuple[str, str], ...] = (
    ("baseline", "dev"),
    ("baseline", "test"),
    ("reflection", "dev"),
    ("best-of-3", "dev"),
    ("prompt-candidate", "dev"),
    ("prompt-candidate", "test"),
    ("skill-candidate", "dev"),
    ("skill-candidate", "test"),
)


def _validate_frozen_replay_coverage(
    manifest: DatasetManifest, records: tuple[ExperimentRecord, ...]
) -> None:
    """Require the pre-registered case/repeat matrix for the canonical evidence."""
    for method, split in _FROZEN_REPLAY_COVERAGE:
        case_ids = tuple(case.case_id for case in manifest.cases if case.split == split)
        expected = {
            f"phase12-exp-replay-{method.lower()}-{repeat}-{case_id}"
            for repeat in range(1, 4)
            for case_id in case_ids
        }
        actual = {
            record.experiment_id
            for record in records
            if (
                record.method == method
                and record.split == split
                and record.model == "deterministic-replay"
            )
        }
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details = []
            if missing:
                details.append(f"missing {len(missing)}")
            if extra:
                details.append(f"unexpected {len(extra)}")
            raise ValueError(
                f"frozen replay coverage mismatch for {method}/{split}: " + ", ".join(details)
            )


def _canonical_report_suffix(output: Path) -> str | None:
    """Validate the optional canonical report set and return its shared suffix."""
    groups = {
        "stage": sorted(output.glob("phase12-stage-report-draft-*.md")),
        "adoption": sorted(output.glob("phase12-adoption-card-*.md")),
        "summary": sorted(output.glob("phase12-summary-*.json")),
    }
    existing = [path for paths in groups.values() for path in paths]
    if not existing:
        return None
    if any(len(paths) != 1 for paths in groups.values()):
        raise ValueError("canonical report set must contain exactly one file of each kind")
    suffixes = {
        groups["stage"][0].stem.removeprefix("phase12-stage-report-draft-"),
        groups["adoption"][0].stem.removeprefix("phase12-adoption-card-"),
        groups["summary"][0].stem.removeprefix("phase12-summary-"),
    }
    if len(suffixes) != 1:
        raise ValueError("canonical report files must share one code suffix")
    return next(iter(suffixes))


def _verify_canonical_summary(
    output: Path,
    manifest: DatasetManifest,
    records: tuple[ExperimentRecord, ...],
    training_artifacts: tuple[TrainingArtifact, ...],
    suffix: str,
) -> None:
    path = output / f"phase12-summary-{suffix}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("canonical summary is not valid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("code_version"), str):
        raise ValueError("canonical summary has an invalid shape")
    expected = build_summary(
        manifest,
        records,
        training_artifacts,
        code_version=payload["code_version"],
    )
    if digest_json(payload) != digest_json(json.loads(canonical_json(expected))):
        raise ValueError("canonical summary does not match current evidence")


def _validate_lab_version(root: Path, version_id: str) -> None:
    if version_id in {"native-agent/A", "skill-registry/v1"}:
        return
    if version_id.startswith("phase12-"):
        read_candidate(root / PHASE12_RELATIVE_ROOT, version_id)
        return
    raise ValueError("unknown lab version")


def _evidence_records(root: Path, evidence_ids: tuple[str, ...]) -> tuple[ExperimentRecord, ...]:
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("selection evidence ids must be unique")
    paths = sorted(
        path
        for path in (root / PHASE12_RELATIVE_ROOT).glob("*.jsonl")
        if path.name != RESERVATION_LEDGER_NAME
    )
    records_by_id: dict[str, ExperimentRecord] = {}
    for path in paths:
        relative = str(path.relative_to(root))
        for record in read_records(root, relative):
            if record.experiment_id in records_by_id:
                raise ValueError("duplicate experiment evidence id")
            records_by_id[record.experiment_id] = record
    missing = sorted(set(evidence_ids) - set(records_by_id))
    if missing:
        raise FileNotFoundError(f"evaluation evidence is missing: {', '.join(missing)}")
    return tuple(records_by_id[evidence_id] for evidence_id in evidence_ids)


def _validate_evidence(
    root: Path,
    evidence_ids: tuple[str, ...],
    *,
    candidate_id: str | None,
    expected_method: str,
    split: str = "dev",
) -> None:
    records = _evidence_records(root, evidence_ids)
    manifest = _manifest(root)
    versions = {record.code_version for record in records}
    if len(versions) != 1:
        raise ValueError("selection evidence must use one experiment code version")
    expected_ids = {
        f"phase12-exp-replay-{expected_method.lower()}-{repeat}-{case.case_id}"
        for repeat in range(1, 4)
        for case in manifest.cases
        if case.split == split
    }
    actual_ids = {record.experiment_id for record in records}
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        details = []
        if missing:
            details.append(f"missing {len(missing)}")
        if extra:
            details.append(f"unexpected {len(extra)}")
        raise ValueError("selection evidence coverage mismatch: " + ", ".join(details))
    for record in records:
        if record.split != split:
            raise ValueError("selection evidence must use the dev split")
        if (
            record.dataset_id != manifest.dataset_id
            or record.dataset_digest != manifest.dataset_digest
        ):
            raise ValueError("selection evidence uses a different dataset")
        if record.method != expected_method or record.candidate_id != candidate_id:
            raise ValueError("selection evidence does not match the selected version")
        if record.status not in {"SUCCEEDED", "REJECTED"}:
            raise ValueError("selection evidence cannot use unknown or incomplete records")
        if candidate_id is not None and record.method == expected_method:
            candidate = read_candidate(root / PHASE12_RELATIVE_ROOT, candidate_id)
            if (
                record.candidate_content_sha256 != candidate.content_sha256
                or record.candidate_boundary_sha256 != candidate.boundary_sha256
            ):
                raise ValueError("selection evidence candidate digest binding mismatch")


def _cmd_evaluate(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    plan: ExperimentPlan | None = None
    plan_path = args.root / PHASE12_RELATIVE_ROOT / "phase12-experiment-manifest.json"
    if plan_path.exists():
        plan = ExperimentPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        if plan.dataset_digest != manifest.dataset_digest or plan.dataset_id != manifest.dataset_id:
            raise ValueError("experiment plan is bound to a different dataset")
        if plan.code_version != code_version():
            raise ValueError("experiment plan is bound to a different code version")
        allowed = plan.dev_methods if args.split == "dev" else plan.test_methods
        if args.method not in allowed:
            raise ValueError("method is not preregistered for the requested split")
    if args.repeats < 1 or args.repeats > 3:
        raise ValueError("evaluation repeats must be between one and three")
    if args.engine == "replay":
        candidate_id = None
        candidate_content = None
        candidate_content_sha256 = None
        candidate_boundary_sha256 = None
        if args.method in {"prompt-candidate", "skill-candidate"}:
            candidate = _candidate_for_method(args.root, args.method)
            candidate_id = candidate.candidate_id
            candidate_content = candidate.content
            candidate_content_sha256 = candidate.content_sha256
            candidate_boundary_sha256 = candidate.boundary_sha256
        records = _replay_records(
            manifest,
            args.split,
            args.method,
            code_version(),
            repeats=args.repeats,
            candidate_id=candidate_id,
            candidate_content=candidate_content,
            candidate_content_sha256=candidate_content_sha256,
            candidate_boundary_sha256=candidate_boundary_sha256,
            case_id=args.case_id,
        )
    else:
        from agent_runtime.providers import ProviderError, provider_for_role

        batch_id = _validated_batch_id(args.batch_id)
        candidate_id = None
        candidate_content = None
        candidate_content_sha256 = None
        candidate_boundary_sha256 = None
        if args.method in {"prompt-candidate", "skill-candidate"}:
            candidate = _candidate_for_method(args.root, args.method)
            candidate_id = candidate.candidate_id
            candidate_content = candidate.content
            candidate_content_sha256 = candidate.content_sha256
            candidate_boundary_sha256 = candidate.boundary_sha256
        try:
            provider = provider_for_role("assist")
        except ProviderError as error:
            raise RuntimeError(f"live provider unavailable: {error.failure_code}") from error
        ledger = ReservationLedger(args.root / PHASE12_RELATIVE_ROOT / RESERVATION_LEDGER_NAME)
        ledger.reconcile_reserved()
        ledger.mark_recorded_matching(_live_record_keys(args.root))
        budget = CallBudget(
            maximum=1_200,
            used=_existing_calls(args.root),
            ledger=ledger,
            batch_id=batch_id,
        )
        cases = tuple(case for case in manifest.cases if case.split == args.split)
        if args.case_id is not None:
            cases = tuple(case for case in cases if case.case_id == args.case_id)
            if not cases:
                raise ValueError("case id is missing from the requested split")
        record_name = f"evaluation-live-{args.method}-{args.split}-{batch_id}.jsonl"
        target = args.root / PHASE12_RELATIVE_ROOT / record_name
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"live batch output already exists: {record_name}")

        def persist(record: ExperimentRecord) -> None:
            append_record(args.root, record_name, record)
            ledger.mark_recorded_matching(record.reservation_keys)

        records = run_live_methods(
            cases,
            provider,
            budget,
            method=args.method,
            code_version=code_version(),
            model=getattr(provider, "_model", getattr(provider, "model", "assist")),
            repeats=args.repeats,
            dataset_id=manifest.dataset_id,
            dataset_digest=manifest.dataset_digest,
            experiment_plan_id=plan.plan_id if plan is not None else None,
            candidate_id=candidate_id,
            candidate_content=candidate_content,
            candidate_content_sha256=candidate_content_sha256,
            candidate_boundary_sha256=candidate_boundary_sha256,
            record_sink=persist,
        )
        path = target
    if args.engine == "replay":
        path = write_records(
            args.root, f"evaluation-{args.engine}-{args.method}-{args.split}.jsonl", records
        )
    return {
        "path": str(path),
        "records": len(records),
        "calls": sum(record.calls for record in records),
    }


def _cmd_freeze_experiment(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    output = args.root / PHASE12_RELATIVE_ROOT
    candidate_paths = sorted((output / "candidates").glob("*.json"))
    candidates = tuple(read_candidate(output, path.stem) for path in candidate_paths)
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    plan = ExperimentPlan(
        plan_id=args.plan_id,
        code_version=code_version(),
        dataset_id=manifest.dataset_id,
        dataset_digest=manifest.dataset_digest,
        model=args.model,
        dev_methods=(
            "baseline",
            "reflection",
            "best-of-3",
            "prompt-candidate",
            "skill-candidate",
        ),
        test_methods=tuple(
            dict.fromkeys(("baseline", "prompt-candidate", "skill-candidate", args.selected_method))
        ),
        selected_method=args.selected_method,
        candidate_ids=candidate_ids,
        verifier_version="replay-verifier-v2",
        reward_version="safe-reward-v1",
    )
    path = write_json_once(
        args.root,
        f"{PHASE12_RELATIVE_ROOT}/phase12-experiment-manifest.json",
        plan.model_dump(mode="json"),
    )
    return {"path": str(path), "plan": plan.model_dump(mode="json")}


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


def _cmd_evaluate_weights(args: argparse.Namespace) -> dict[str, object]:
    manifest = _manifest(args.root)
    verify_manifest(manifest)
    output = args.root / PHASE12_RELATIVE_ROOT
    weight_path = output / f"weights-{args.method}-{args.seed}.json"
    metadata_path = weight_path.with_suffix(".metadata.json")
    if not weight_path.is_file() or weight_path.is_symlink():
        raise FileNotFoundError(f"training weights are missing: {weight_path.name}")
    if not metadata_path.is_file() or metadata_path.is_symlink():
        raise FileNotFoundError(f"training metadata is missing: {metadata_path.name}")
    metadata = TrainingArtifact.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    if metadata.method != args.method or metadata.seed != args.seed:
        raise ValueError("weight metadata does not match the requested method and seed")
    record_name = f"evaluation-weights-{args.method}-{args.seed}-{args.split}.jsonl"
    records = evaluate_weight_artifact(
        manifest,
        weight_path,
        method=args.method,
        seed=args.seed,
        split=args.split,
        code_version=code_version(),
        artifact_id=metadata.artifact_id,
    )
    path = write_records(args.root, record_name, records)
    return {
        "path": str(path),
        "records": len(records),
        "method": args.method,
        "seed": args.seed,
        "split": args.split,
        "weight_sha256": metadata.weight_sha256,
    }


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
    record_files = sorted(
        path for path in output.glob("*.jsonl") if path.name != RESERVATION_LEDGER_NAME
    )
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
            if (
                record.candidate_content_sha256 != record_candidate.content_sha256
                or record.candidate_boundary_sha256 != record_candidate.boundary_sha256
            ):
                raise ValueError("candidate experiment digest binding mismatch")
    verify_records(all_records, manifest)
    canonical_suffix = _canonical_report_suffix(output)
    if canonical_suffix is not None:
        _validate_frozen_replay_coverage(manifest, tuple(all_records))
    ledger_path = output / RESERVATION_LEDGER_NAME
    if ledger_path.exists():
        ledger = ReservationLedger(ledger_path)
        if any(state != "RECORDED" for _, state in ledger.states()):
            raise ValueError("live reservation ledger contains an unrecorded call")
        if _existing_calls(args.root) > 1_200:
            raise ValueError("cumulative model call budget exceeded")
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
        evidence_version = (
            selection.selected_id if selection.action == "SELECT" else selection.previous_id
        )
        evidence_candidate = (
            candidates_by_id.get(evidence_version)
            if evidence_version.startswith("phase12-")
            else None
        )
        _validate_evidence(
            args.root,
            selection.evidence_ids,
            candidate_id=evidence_version if evidence_candidate is not None else None,
            expected_method=(
                "prompt-candidate"
                if evidence_candidate is not None and evidence_candidate.kind == "PROMPT"
                else "skill-candidate"
                if evidence_candidate is not None
                else "baseline"
            ),
        )
    active = read_active_version(output)
    if active is not None:
        active_selection = read_selection(output, active.selection_id)
        if active_selection.selected_id != active.version_id:
            raise ValueError("active lab version is not backed by its selection")
        if active_selection.version_digests.get(active.version_id) != active.content_sha256:
            raise ValueError("active lab version digest is not backed by its selection")
        if version_content_digest(output, active.version_id) != active.content_sha256:
            raise ValueError("active lab version content digest mismatch")
    weight_count = 0
    training_artifacts: list[TrainingArtifact] = []
    for path in sorted(output.glob("weights-*.json")):
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
        training_artifacts.append(metadata)
        weight_count += 1
    if canonical_suffix is not None:
        _verify_canonical_summary(
            output,
            manifest,
            tuple(all_records),
            tuple(training_artifacts),
            canonical_suffix,
        )
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
        if path.name == RESERVATION_LEDGER_NAME:
            continue
        records.extend(read_records(args.root, str(path.relative_to(args.root))))
    verify_records(records, manifest)
    _validate_frozen_replay_coverage(manifest, tuple(records))
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
    evaluate.add_argument("--batch-id", default=None)
    evaluate.add_argument("--case-id", default=None)
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
    weight_eval = sub.add_parser("evaluate-weights")
    weight_eval.add_argument("--method", choices=("sft", "dpo", "reinforce"), required=True)
    weight_eval.add_argument("--seed", type=int, choices=(17, 29, 43), default=17)
    weight_eval.add_argument("--split", choices=("train", "dev", "test"), default="dev")
    freeze = sub.add_parser("freeze-experiment")
    freeze.add_argument("--plan-id", default="phase12-plan-r5-v1")
    freeze.add_argument("--model", default="glm-5.3-flash", help="frozen assist model identifier")
    freeze.add_argument(
        "--selected-method",
        choices=("baseline", "reflection", "best-of-3", "prompt-candidate", "skill-candidate"),
        default="baseline",
    )
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
            _validate_evidence(
                args.root,
                tuple(args.evidence),
                candidate_id=candidate.candidate_id,
                expected_method=(
                    "prompt-candidate" if candidate.kind == "PROMPT" else "skill-candidate"
                ),
            )
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
            selection_path, active_path = apply_selection(
                args.root / PHASE12_RELATIVE_ROOT, selection
            )
            result = {
                "path": str(selection_path),
                "selection": selection.model_dump(mode="json"),
            }
            result["active_path"] = str(active_path)
        elif args.command == "rollback-lab":
            current = read_selection(args.root / PHASE12_RELATIVE_ROOT, args.selection_id)
            evidence_version = current.selected_id
            evidence_candidate = (
                read_candidate(args.root / PHASE12_RELATIVE_ROOT, evidence_version)
                if evidence_version.startswith("phase12-")
                else None
            )
            _validate_evidence(
                args.root,
                tuple(args.evidence),
                candidate_id=evidence_version if evidence_candidate is not None else None,
                expected_method=(
                    "prompt-candidate"
                    if evidence_candidate is not None and evidence_candidate.kind == "PROMPT"
                    else "skill-candidate"
                    if evidence_candidate is not None
                    else "baseline"
                ),
            )
            selection, path = rollback_selection(
                args.root / PHASE12_RELATIVE_ROOT, args.selection_id, args.evidence, args.reason
            )
            result = {"path": str(path), "selection": selection.model_dump(mode="json")}
        elif args.command == "train":
            result = _cmd_train(args)
        elif args.command == "evaluate-weights":
            result = _cmd_evaluate_weights(args)
        elif args.command == "freeze-experiment":
            result = _cmd_freeze_experiment(args)
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
