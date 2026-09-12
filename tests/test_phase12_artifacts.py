from __future__ import annotations

from pathlib import Path

import pytest

from labs.self_improvement.artifacts import (
    PHASE12_RELATIVE_ROOT,
    read_manifest,
    read_records,
    verify_manifest,
    verify_records,
    write_manifest,
    write_records,
)
from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.evaluation import evaluate_replay_cases
from labs.self_improvement.replay import deterministic_policy


def test_manifest_and_records_round_trip_under_phase12_root(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("artifact-test")
    path = write_manifest(tmp_path, manifest)
    assert path == tmp_path / PHASE12_RELATIVE_ROOT / "dataset-phase12-synthetic-v2.json"
    verify_manifest(read_manifest(tmp_path, str(path.relative_to(tmp_path))))
    records = evaluate_replay_cases(
        manifest.cases[:2],
        deterministic_policy,
        code_version="artifact-test",
        dataset_digest=manifest.dataset_digest,
    )
    record_path = write_records(tmp_path, "replay.jsonl", records)
    loaded = read_records(tmp_path, str(record_path.relative_to(tmp_path)))
    verify_records(loaded, manifest)


def test_artifact_writes_do_not_overwrite_or_escape(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("artifact-test")
    write_manifest(tmp_path, manifest)
    with pytest.raises(FileExistsError):
        write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError):
        read_manifest(tmp_path, "output/other.json")


def test_artifact_paths_reject_symlink_components(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "output").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_manifest(tmp_path, build_synthetic_manifest("artifact-test"))


def test_tampered_manifest_and_duplicate_records_are_rejected(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("artifact-test")
    tampered = manifest.model_copy(update={"dataset_digest": "0" * 64})
    with pytest.raises(ValueError, match="digest"):
        verify_manifest(tampered)
    records = evaluate_replay_cases(
        manifest.cases[:1],
        deterministic_policy,
        code_version="artifact-test",
        dataset_digest=manifest.dataset_digest,
    )
    with pytest.raises(ValueError, match="unique"):
        verify_records((*records, records[0]), manifest)
