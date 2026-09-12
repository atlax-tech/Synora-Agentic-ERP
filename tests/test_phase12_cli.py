from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from labs.self_improvement.artifacts import read_records, write_records
from labs.self_improvement.cli import (
    _canonical_report_suffix,
    _manifest,
    _validate_frozen_replay_coverage,
    main,
)
from labs.self_improvement.contracts import DatasetManifest, ExperimentRecord, TrainingArtifact
from labs.self_improvement.reporting import write_reports


def _complete_replay_matrix(manifest: DatasetManifest) -> list[ExperimentRecord]:
    methods = (
        ("baseline", "dev"),
        ("baseline", "test"),
        ("reflection", "dev"),
        ("best-of-3", "dev"),
        ("prompt-candidate", "dev"),
        ("prompt-candidate", "test"),
        ("skill-candidate", "dev"),
        ("skill-candidate", "test"),
    )
    records: list[ExperimentRecord] = []
    for method, split in methods:
        for repeat in range(1, 4):
            for case in manifest.cases:
                if case.split != split:
                    continue
                records.append(
                    ExperimentRecord(
                        experiment_id=f"phase12-exp-replay-{method}-{repeat}-{case.case_id}",
                        code_version="coverage-test",
                        dataset_id=manifest.dataset_id,
                        dataset_digest=manifest.dataset_digest,
                        split=split,
                        method=method,
                        model="deterministic-replay",
                        repeat=repeat,
                        status="SUCCEEDED",
                        output_action="FINISH",
                        verifier_passed=True,
                        safety_passed=True,
                        score=1.0,
                        elapsed_ms=0.0,
                        calls=0,
                    )
                )
    return records


def _write_historical_failure(root: Path) -> None:
    source = root / "output" / "phase11" / "phase11-live-dom-glm-2bb2aa2.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"failure_code":"ACTION_REJECTED"}\n', encoding="utf-8")


def test_cli_prepare_evaluate_train_and_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "evaluate", "--split", "dev"]) == 0
    assert main(["--root", str(tmp_path), "train", "--method", "sft", "--seed", "17"]) == 0
    assert main(["--root", str(tmp_path), "verify-artifacts"]) == 0
    output = capsys.readouterr().out
    assert '"status": "PASS"' in output


def test_cli_candidate_selection_and_rollback(tmp_path: Path) -> None:
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "make-candidates"]) == 0
    candidate_dir = tmp_path / "output" / "phase12" / "candidates"
    candidate_id = json.loads(next(candidate_dir.glob("phase12-prompt-*.json")).read_text())[
        "candidate_id"
    ]
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "evaluate",
                "--method",
                "prompt-candidate",
                "--split",
                "dev",
                "--repeats",
                "3",
            ]
        )
        == 0
    )
    candidate_evidence = [
        json.loads(line)["experiment_id"]
        for line in (
            tmp_path / "output" / "phase12" / "evaluation-replay-prompt-candidate-dev.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    evidence_ids = candidate_evidence
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "select-lab",
                "--candidate-id",
                candidate_id,
                "--evidence",
                *evidence_ids,
                "--reason",
                "dev",
            ]
        )
        == 0
    )
    selection_dir = tmp_path / "output" / "phase12" / "selections"
    selection_id = json.loads(next(selection_dir.glob("*.json")).read_text())["selection_id"]
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "rollback-lab",
                "--selection-id",
                selection_id,
                "--evidence",
                *evidence_ids,
                "--reason",
                "regression",
            ]
        )
        == 0
    )


def test_cli_candidate_generation_requires_reviewed_failures(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "make-candidates"]) == 2
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "make-candidates"]) == 0
    candidate_dir = tmp_path / "output" / "phase12" / "candidates"
    payload = json.loads(next(candidate_dir.glob("phase12-prompt-*.json")).read_text())
    assert payload["source_case_ids"][0].startswith("phase12-historical-")


def test_cli_verify_rejects_tampered_audit_artifact(tmp_path: Path) -> None:
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    audit = tmp_path / "output" / "phase12" / "audited-failures.json"
    payload = json.loads(audit.read_text(encoding="utf-8"))
    payload["records"][0]["review_reason"] = "tampered"
    audit.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["--root", str(tmp_path), "verify-artifacts"]) == 2


def test_cli_verify_rejects_candidate_with_unaudited_source(tmp_path: Path) -> None:
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "make-candidates"]) == 0
    candidate_path = next((tmp_path / "output" / "phase12" / "candidates").glob("*.json"))
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    payload["source_case_ids"] = ["phase12-historical-unaudited"]
    candidate_path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["--root", str(tmp_path), "verify-artifacts"]) == 2


def test_cli_default_evaluate_does_not_require_provider(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "evaluate", "--engine", "replay", "--split", "test"]) == 0


def test_cli_rejects_tampered_training_metadata(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "train", "--method", "sft", "--seed", "17"]) == 0
    metadata = tmp_path / "output" / "phase12" / "weights-sft-17.metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["weight_sha256"] = "0" * 64
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["--root", str(tmp_path), "verify-artifacts"]) == 2


def test_cli_candidate_method_and_repeats_are_bounded(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "make-candidates"]) == 0
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "evaluate",
                "--method",
                "prompt-candidate",
                "--split",
                "dev",
                "--repeats",
                "3",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "evaluate",
                "--method",
                "skill-candidate",
                "--split",
                "dev",
                "--repeats",
                "4",
            ]
        )
        == 2
    )


def test_cli_report_rejects_trimmed_frozen_evidence(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "evaluate",
                "--method",
                "baseline",
                "--split",
                "dev",
                "--repeats",
                "3",
            ]
        )
        == 0
    )
    evidence = tmp_path / "output" / "phase12" / "evaluation-replay-baseline-dev.jsonl"
    evidence.write_text("\n".join(evidence.read_text().splitlines()[:-1]) + "\n", encoding="utf-8")
    manifest = _manifest(tmp_path)
    records = read_records(tmp_path, "output/phase12/evaluation-replay-baseline-dev.jsonl")
    with pytest.raises(ValueError, match="coverage mismatch"):
        _validate_frozen_replay_coverage(manifest, records)


def test_frozen_coverage_ignores_live_baseline_records(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    manifest = _manifest(tmp_path)
    records = _complete_replay_matrix(manifest)
    records.append(
        ExperimentRecord(
            experiment_id="phase12-exp-live-coverage-test",
            code_version="coverage-test",
            dataset_id=manifest.dataset_id,
            dataset_digest=manifest.dataset_digest,
            split="test",
            method="baseline",
            model="assist",
            repeat=1,
            status="UNKNOWN",
            verifier_passed=False,
            safety_passed=True,
            score=-1.0,
            failure_code="TIMEOUT",
            elapsed_ms=1.0,
            calls=1,
        )
    )
    _validate_frozen_replay_coverage(manifest, tuple(records))


def test_cli_verify_uses_stable_training_artifact_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "make-candidates"]) == 0
    manifest = _manifest(tmp_path)
    records = _complete_replay_matrix(manifest)
    candidates = {
        "prompt-candidate": json.loads(
            next(
                (tmp_path / "output" / "phase12" / "candidates").glob("phase12-prompt-*.json")
            ).read_text(encoding="utf-8")
        ),
        "skill-candidate": json.loads(
            next(
                (tmp_path / "output" / "phase12" / "candidates").glob("phase12-skill-*.json")
            ).read_text(encoding="utf-8")
        ),
    }
    records = [
        ExperimentRecord(
            **{
                **record.model_dump(),
                "candidate_id": candidates[record.method]["candidate_id"]
                if record.method in candidates
                else None,
                "candidate_content_sha256": candidates[record.method]["content_sha256"]
                if record.method in candidates
                else None,
                "candidate_boundary_sha256": candidates[record.method]["boundary_sha256"]
                if record.method in candidates
                else None,
            }
        )
        for record in records
    ]
    write_records(tmp_path, "evaluation-replay-all.jsonl", records)
    assert main(["--root", str(tmp_path), "train", "--method", "sft", "--seed", "17"]) == 0
    assert main(["--root", str(tmp_path), "train", "--method", "dpo", "--seed", "17"]) == 0
    output = tmp_path / "output" / "phase12"
    artifacts = tuple(
        TrainingArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(output.glob("weights-*.metadata.json"))
    )
    write_reports(tmp_path, manifest, records, artifacts, code_version="coverage-test")

    original_glob = Path.glob

    def reverse_weight_glob(path: Path, pattern: str) -> object:
        result = original_glob(path, pattern)
        if path == output and pattern == "weights-*.json":
            return iter(reversed(tuple(result)))
        return result

    monkeypatch.setattr(Path, "glob", reverse_weight_glob)
    assert main(["--root", str(tmp_path), "verify-artifacts"]) == 0


def test_canonical_reports_must_have_one_shared_suffix(tmp_path: Path) -> None:
    output = tmp_path / "output" / "phase12"
    output.mkdir(parents=True)
    (output / "phase12-stage-report-draft-a.md").write_text("stage", encoding="utf-8")
    (output / "phase12-adoption-card-a.md").write_text("adoption", encoding="utf-8")
    (output / "phase12-summary-b.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="share one code suffix"):
        _canonical_report_suffix(output)


def test_cli_selection_rejects_parent_version_mismatch(tmp_path: Path) -> None:
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "make-candidates"]) == 0
    candidate_path = next(
        (tmp_path / "output" / "phase12" / "candidates").glob("phase12-prompt-*.json")
    )
    candidate_id = json.loads(candidate_path.read_text(encoding="utf-8"))["candidate_id"]
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "select-lab",
                "--candidate-id",
                candidate_id,
                "--previous-id",
                "skill-registry/v1",
                "--evidence",
                "phase12-exp-unknown",
                "--reason",
                "mismatch",
            ]
        )
        == 2
    )


def test_cli_selection_rejects_unrelated_evidence(tmp_path: Path) -> None:
    _write_historical_failure(tmp_path)
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "make-candidates"]) == 0
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "evaluate",
                "--method",
                "baseline",
                "--split",
                "dev",
            ]
        )
        == 0
    )
    candidate_path = next(
        (tmp_path / "output" / "phase12" / "candidates").glob("phase12-prompt-*.json")
    )
    candidate_id = json.loads(candidate_path.read_text(encoding="utf-8"))["candidate_id"]
    evidence_id = json.loads(
        (tmp_path / "output" / "phase12" / "evaluation-replay-baseline-dev.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )["experiment_id"]
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "select-lab",
                "--candidate-id",
                candidate_id,
                "--evidence",
                evidence_id,
                "--reason",
                "wrong method",
            ]
        )
        == 2
    )


def test_cli_module_runs_from_clean_python_process(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "labs.self_improvement.cli",
            "--root",
            str(tmp_path),
            "prepare-data",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
