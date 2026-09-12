"""Versioned Prompt/Skill candidates confined to the Phase 12 lab."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from agent_runtime.agent.prompting import PROMPT_REGISTRY

from .contracts import CandidateVersion, LabSelection, digest_bytes, digest_json, safe_output_path
from .replay import READ_ACTIONS

_FORBIDDEN_CANDIDATE_MARKERS = (
    "boundary",
    "output_contract",
    "permission",
    "authorization",
    "writer",
    "http",
    "sql",
    "shell",
    "tool allowlist",
)


def lab_boundary_digest() -> str:
    profile = PROMPT_REGISTRY.resolve("native-agent", variant="A")
    return digest_json(
        {
            "boundary": profile.layer_hash("boundary"),
            "recovery": profile.layer_hash("recovery"),
            "output_contract": profile.layer_hash("output_contract"),
            "read_actions": sorted(READ_ACTIONS),
        }
    )


def _validate_candidate_content(content: str) -> None:
    lowered = content.casefold()
    if not content.strip() or len(content) > 4_000:
        raise ValueError("candidate content is empty or too large")
    if any(marker in lowered for marker in _FORBIDDEN_CANDIDATE_MARKERS):
        raise ValueError("candidate attempts to change a protected boundary")
    if "[decision" in lowered or "[skill" in lowered:
        raise ValueError("candidate cannot inject a layer marker")


def make_prompt_candidate(source_case_ids: Iterable[str], content: str) -> CandidateVersion:
    sources = tuple(source_case_ids)
    _validate_candidate_content(content)
    if not sources:
        raise ValueError("candidate needs at least one reviewed source")
    content_digest = digest_bytes(content.encode("utf-8"))
    candidate_id = f"phase12-prompt-{content_digest[:16]}"
    return CandidateVersion(
        candidate_id=candidate_id,
        kind="PROMPT",
        parent_id="native-agent/A",
        source_case_ids=sources,
        content=content,
        content_sha256=content_digest,
        boundary_sha256=lab_boundary_digest(),
    )


def make_skill_candidate(source_case_ids: Iterable[str], content: str) -> CandidateVersion:
    sources = tuple(source_case_ids)
    _validate_candidate_content(content)
    if not sources:
        raise ValueError("candidate needs at least one reviewed source")
    content_digest = digest_bytes(content.encode("utf-8"))
    candidate_id = f"phase12-skill-{content_digest[:16]}"
    return CandidateVersion(
        candidate_id=candidate_id,
        kind="SKILL",
        parent_id="skill-registry/v1",
        source_case_ids=sources,
        content=content,
        content_sha256=content_digest,
        boundary_sha256=lab_boundary_digest(),
    )


def validate_candidate(candidate: CandidateVersion) -> CandidateVersion:
    _validate_candidate_content(candidate.content)
    if candidate.boundary_sha256 != lab_boundary_digest():
        raise ValueError("candidate boundary digest is stale")
    if candidate.kind == "PROMPT" and candidate.parent_id != "native-agent/A":
        raise ValueError("prompt candidate parent is not the fixed lab profile")
    if candidate.kind == "SKILL" and candidate.parent_id != "skill-registry/v1":
        raise ValueError("skill candidate parent is not the fixed lab registry")
    return candidate


def write_candidate(root: Path, candidate: CandidateVersion) -> Path:
    validate_candidate(candidate)
    path = safe_output_path(root, f"candidates/{candidate.candidate_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.write_text(
        json.dumps(candidate.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def read_candidate(root: Path, candidate_id: str) -> CandidateVersion:
    if "/" in candidate_id or ".." in candidate_id:
        raise ValueError("candidate id is not a file path")
    path = safe_output_path(root, f"candidates/{candidate_id}.json")
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    candidate = CandidateVersion.model_validate_json(path.read_text(encoding="utf-8"))
    return validate_candidate(candidate)


def write_selection(root: Path, selection: LabSelection) -> Path:
    path = safe_output_path(root, f"selections/{selection.selection_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.write_text(
        json.dumps(selection.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def build_selection(
    previous_id: str,
    selected_id: str,
    evidence_ids: Iterable[str],
    reason: str,
    *,
    action: str = "SELECT",
) -> LabSelection:
    evidence = tuple(evidence_ids)
    if not evidence:
        raise ValueError("selection requires evaluation evidence")
    selection_id = (
        "phase12-selection-"
        + digest_json({"previous": previous_id, "selected": selected_id, "evidence": evidence})[:16]
    )
    return LabSelection(
        selection_id=selection_id,
        previous_id=previous_id,
        selected_id=selected_id,
        action=action,  # type: ignore[arg-type]
        evidence_ids=evidence,
        reason=reason,
    )
