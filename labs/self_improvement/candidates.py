"""Versioned Prompt/Skill candidates confined to the Phase 12 lab."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from agent_runtime.agent.prompting import PROMPT_REGISTRY
from agent_runtime.skills.registry import SkillRegistry

from .contracts import (
    ActiveLabVersion,
    CandidateVersion,
    LabSelection,
    digest_bytes,
    digest_json,
    safe_output_path,
)
from .replay import READ_ACTIONS, Policy, candidate_policy, deterministic_policy

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
_ACTIVE_VERSION_NAME = "active-version.json"


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
    if ".." in content or content.lstrip().startswith("/"):
        raise ValueError("candidate contains a path traversal")
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
    if candidate.candidate_id != candidate_id:
        raise ValueError("candidate filename does not match its ID")
    return validate_candidate(candidate)


def write_selection(root: Path, selection: LabSelection) -> Path:
    path = safe_output_path(root, f"selections/{selection.selection_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    encoded = (
        json.dumps(selection.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return path


def read_selection(root: Path, selection_id: str) -> LabSelection:
    if "/" in selection_id or ".." in selection_id:
        raise ValueError("selection id is not a file path")
    path = safe_output_path(root, f"selections/{selection_id}.json")
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    selection = LabSelection.model_validate_json(path.read_text(encoding="utf-8"))
    if selection.selection_id != selection_id:
        raise ValueError("selection filename does not match its ID")
    return selection


def read_active_version(root: Path) -> ActiveLabVersion | None:
    path = safe_output_path(root, _ACTIVE_VERSION_NAME)
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError("active lab version must be a regular file")
    return ActiveLabVersion.model_validate_json(path.read_text(encoding="utf-8"))


def _atomic_write_active(path: Path, payload: ActiveLabVersion) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError("active lab version cannot be a symlink")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".active-version-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(
                json.dumps(
                    payload.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, indent=2
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def activate_selection(root: Path, selection: LabSelection) -> Path:
    """Atomically move the lab pointer to a selected version after digest checks."""
    expected_digest = selection.version_digests.get(selection.selected_id)
    previous_digest = selection.version_digests.get(selection.previous_id)
    if expected_digest is None or previous_digest is None:
        raise ValueError("selection must include both version digests")
    if version_content_digest(root, selection.selected_id) != expected_digest:
        raise ValueError("selected version content digest mismatch")
    if version_content_digest(root, selection.previous_id) != previous_digest:
        raise ValueError("previous version content digest mismatch")
    current = read_active_version(root)
    if current is not None:
        if current.version_id != selection.previous_id or current.content_sha256 != previous_digest:
            raise ValueError("active lab version is stale")
    payload = ActiveLabVersion(
        version_id=selection.selected_id,
        content_sha256=expected_digest,
        selection_id=selection.selection_id,
        action=selection.action,
    )
    path = safe_output_path(root, _ACTIVE_VERSION_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_active(path, payload)
    return path


def apply_selection(root: Path, selection: LabSelection) -> tuple[Path, Path]:
    """Write one receipt and pointer, cleaning up on a normal activation failure."""
    receipt = write_selection(root, selection)
    try:
        active = activate_selection(root, selection)
    except Exception:
        receipt.unlink(missing_ok=True)
        raise
    return receipt, active


def load_active_policy(root: Path) -> Policy:
    """Load the explicitly selected lab version for replay only."""
    active = read_active_version(root)
    if active is None:
        raise FileNotFoundError("active lab version is not selected")
    if version_content_digest(root, active.version_id) != active.content_sha256:
        raise ValueError("active lab version content digest mismatch")
    if active.version_id.startswith("phase12-"):
        return candidate_policy(read_candidate(root, active.version_id).content)
    if active.version_id in {"native-agent/A", "skill-registry/v1"}:
        return deterministic_policy
    raise ValueError("unknown active lab version")


def version_content_digest(root: Path, version_id: str) -> str:
    """Return the current content digest for a lab version identifier."""
    if version_id == "native-agent/A":
        return str(PROMPT_REGISTRY.resolve("native-agent", variant="A").profile_hash)
    if version_id == "skill-registry/v1":
        registry = SkillRegistry()
        manifests = tuple(
            manifest.model_dump(mode="json") for manifest in registry.list_manifests()
        )
        return digest_json({"registry_version": registry.version, "manifests": manifests})
    if version_id.startswith("phase12-"):
        return read_candidate(root, version_id).content_sha256
    raise ValueError("unknown lab version")


def build_selection(
    previous_id: str,
    selected_id: str,
    evidence_ids: Iterable[str],
    reason: str,
    *,
    action: str = "SELECT",
    version_digests: Mapping[str, str] | None = None,
) -> LabSelection:
    evidence = tuple(evidence_ids)
    if not evidence:
        raise ValueError("selection requires evaluation evidence")
    digests = dict(version_digests or {})
    if not set(digests).issubset({previous_id, selected_id}):
        raise ValueError("selection digest references an unrelated version")
    selection_id = (
        "phase12-selection-"
        + digest_json(
            {
                "previous": previous_id,
                "selected": selected_id,
                "evidence": evidence,
                "version_digests": digests,
            }
        )[:16]
    )
    return LabSelection(
        selection_id=selection_id,
        previous_id=previous_id,
        selected_id=selected_id,
        action=action,  # type: ignore[arg-type]
        evidence_ids=evidence,
        version_digests=digests,
        reason=reason,
    )


def rollback_selection(
    root: Path,
    selection_id: str,
    evidence_ids: Iterable[str],
    reason: str,
) -> tuple[LabSelection, Path]:
    """Create a new receipt that restores the exact parent of a selection."""
    current = read_selection(root, selection_id)
    if current.action == "ROLLBACK":
        raise ValueError("cannot roll back a rollback receipt")
    for version_id, expected_digest in current.version_digests.items():
        if version_content_digest(root, version_id) != expected_digest:
            raise ValueError(f"version content changed: {version_id}")
    rollback_digests = {
        version_id: current.version_digests[version_id]
        for version_id in (current.selected_id, current.previous_id)
        if version_id in current.version_digests
    }
    rollback = build_selection(
        current.selected_id,
        current.previous_id,
        evidence_ids,
        reason,
        action="ROLLBACK",
        version_digests=rollback_digests,
    )
    path, _ = apply_selection(root, rollback)
    return rollback, path
