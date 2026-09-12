from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from labs.self_improvement.artifacts import read_records
from labs.self_improvement.cli import _manifest, _validate_frozen_replay_coverage, main


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
    candidate_evidence = [
        json.loads(line)["experiment_id"]
        for line in (
            tmp_path / "output" / "phase12" / "evaluation-replay-prompt-candidate-dev.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    baseline_evidence = [
        json.loads(line)["experiment_id"]
        for line in (tmp_path / "output" / "phase12" / "evaluation-replay-baseline-dev.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    evidence_ids = [*candidate_evidence, *baseline_evidence]
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
