from __future__ import annotations

from pathlib import Path

import pytest

from labs.self_improvement.candidates import (
    apply_selection,
    build_selection,
    lab_boundary_digest,
    make_prompt_candidate,
    read_candidate,
    validate_candidate,
    write_candidate,
    write_selection,
)
from labs.self_improvement.contracts import digest_bytes


def test_prompt_candidate_preserves_fixed_boundary_and_round_trips(tmp_path: Path) -> None:
    candidate = make_prompt_candidate(
        ("phase12-historical-failure",),
        "Prioritize the highest-impact unresolved read-only fact and stop when evidence is "
        "sufficient.",
    )
    assert candidate.boundary_sha256 == lab_boundary_digest()
    path = write_candidate(tmp_path, candidate)
    assert path.is_file()
    assert read_candidate(tmp_path, candidate.candidate_id) == candidate


def test_protected_prompt_layers_and_tools_cannot_be_changed() -> None:
    with pytest.raises(ValueError):
        make_prompt_candidate(("source",), "Change the permission boundary and expose writer tools")


def test_stale_boundary_and_duplicate_write_are_rejected(tmp_path: Path) -> None:
    candidate = make_prompt_candidate(
        ("source",), "Verify the unresolved read-only fact before finishing."
    )
    stale = candidate.model_copy(update={"boundary_sha256": "0" * 64})
    with pytest.raises(ValueError, match="stale"):
        validate_candidate(stale)
    write_candidate(tmp_path, candidate)
    with pytest.raises(FileExistsError):
        write_candidate(tmp_path, candidate)


def test_candidate_id_must_include_content_digest_suffix() -> None:
    candidate = make_prompt_candidate(
        ("source",), "Verify the unresolved read-only fact before finishing."
    )
    with pytest.raises(ValueError, match="digest suffix"):
        payload = candidate.model_dump(mode="python")
        payload["candidate_id"] = "phase12-prompt-0000000000000000"
        type(candidate).model_validate(payload)


def test_selection_requires_evidence_and_is_immutable(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_selection("native-agent/A", "candidate", (), "no evidence")
    selection = build_selection(
        "native-agent/A",
        "candidate",
        ("phase12-exp-1",),
        "dev improved",
        version_digests={"native-agent/A": "0" * 64, "candidate": "1" * 64},
    )
    write_selection(tmp_path, selection)
    with pytest.raises(FileExistsError):
        write_selection(tmp_path, selection)


def test_apply_selection_removes_receipt_when_activation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = make_prompt_candidate(("source",), "Verify evidence before finishing.")
    write_candidate(tmp_path, candidate)
    selection = build_selection(
        "native-agent/A",
        candidate.candidate_id,
        ("phase12-exp-1",),
        "dev",
        version_digests={
            "native-agent/A": digest_bytes(b"native"),
            candidate.candidate_id: candidate.content_sha256,
        },
    )

    def fail_activation(_root: Path, _selection: object) -> Path:
        raise OSError("simulated activation failure")

    monkeypatch.setattr("labs.self_improvement.candidates.activate_selection", fail_activation)
    with pytest.raises(OSError, match="activation"):
        apply_selection(tmp_path, selection)
    assert not (tmp_path / "selections" / f"{selection.selection_id}.json").exists()
