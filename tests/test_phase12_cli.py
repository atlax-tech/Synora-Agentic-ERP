from __future__ import annotations

import json
from pathlib import Path

from labs.self_improvement.cli import main


def test_cli_prepare_evaluate_train_and_verify(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "audit-data"]) == 0
    assert main(["--root", str(tmp_path), "evaluate", "--split", "dev"]) == 0
    assert main(["--root", str(tmp_path), "train", "--method", "sft", "--seed", "17"]) == 0
    assert main(["--root", str(tmp_path), "verify-artifacts"]) == 0
    output = capsys.readouterr().out
    assert '"status": "PASS"' in output


def test_cli_candidate_selection_and_rollback(tmp_path: Path) -> None:
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
                "select-lab",
                "--candidate-id",
                candidate_id,
                "--evidence",
                "exp-1",
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
                "exp-2",
                "--reason",
                "regression",
            ]
        )
        == 0
    )


def test_cli_default_evaluate_does_not_require_provider(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "evaluate", "--engine", "replay", "--split", "test"]) == 0


def test_cli_rejects_tampered_training_metadata(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
    assert main(["--root", str(tmp_path), "train", "--method", "sft", "--seed", "17"]) == 0
    metadata = tmp_path / "output" / "phase12" / "weights-sft-17.metadata.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["weight_sha256"] = "0" * 64
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["--root", str(tmp_path), "verify-artifacts"]) == 2


def test_cli_candidate_method_and_repeats_are_bounded(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "prepare-data"]) == 0
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
