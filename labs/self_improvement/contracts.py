"""Strict, content-addressed contracts for the Phase 12 lab."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

CaseKind = Literal[
    "COMPLETE_READ",
    "MISSING_INPUT",
    "DUPLICATE_NO_PROGRESS",
    "TOOL_UNKNOWN",
    "STALE_CONFLICT",
    "UNTRUSTED_INJECTION",
]
ReviewStatus = Literal["ACCEPTED", "REJECTED", "BACKGROUND_ONLY"]
SourceKind = Literal["HISTORICAL_FAILURE", "SYNTHETIC", "SYNTHETIC_DERIVED"]
SplitName = Literal["train", "dev", "test"]
CandidateKind = Literal["PROMPT", "SKILL"]
SelectionAction = Literal["SELECT", "ROLLBACK"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_json(value: object) -> str:
    return digest_bytes(canonical_json(value).encode("utf-8"))


def finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("numeric value must be finite")
    return value


def _tuple_from_json(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


class ReviewedCase(StrictModel):
    schema_version: Literal["1"] = "1"
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,119}$")
    source_path: str = Field(min_length=1, max_length=240)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_kind: SourceKind
    group_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    kind: CaseKind
    review_status: ReviewStatus
    derivation: Literal["DIRECT", "SYNTHETIC_DERIVED", "NONE"] = "NONE"
    input_text: str = Field(min_length=1, max_length=4_000)
    expected_action: str = Field(min_length=1, max_length=80)
    expected_status: str = Field(min_length=1, max_length=80)
    failure_code: str | None = Field(default=None, max_length=120)
    review_reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_derivation(self) -> ReviewedCase:
        if self.source_kind == "SYNTHETIC_DERIVED" and self.derivation != "SYNTHETIC_DERIVED":
            raise ValueError("derived records must declare their derivation")
        if self.review_status == "ACCEPTED" and self.expected_status == "UNKNOWN":
            raise ValueError("accepted cases require a known scoring status")
        return self


class DatasetCase(StrictModel):
    schema_version: Literal["1"] = "1"
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,119}$")
    group_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    split: SplitName
    kind: CaseKind
    source_kind: SourceKind
    source_case_id: str | None = Field(default=None, max_length=120)
    input_text: str = Field(min_length=1, max_length=4_000)
    observable_facts: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_json)] = Field(
        default_factory=tuple, max_length=16
    )
    expected_action: str = Field(min_length=1, max_length=80)
    expected_status: str = Field(min_length=1, max_length=80)
    oracle: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_oracle(self) -> DatasetCase:
        if any(len(key) > 80 or len(value) > 200 for key, value in self.oracle.items()):
            raise ValueError("oracle fields are bounded")
        if any(not fact or len(fact) > 200 for fact in self.observable_facts):
            raise ValueError("observable facts are bounded and non-empty")
        if len(set(self.observable_facts)) != len(self.observable_facts):
            raise ValueError("observable facts must be unique")
        if any(
            marker in fact.casefold()
            for fact in self.observable_facts
            for marker in ("expected_action=", "expected_status=", "oracle=", "case_kind=")
        ):
            raise ValueError("observable facts cannot contain scoring labels")
        if self.source_kind == "HISTORICAL_FAILURE" and not self.source_case_id:
            raise ValueError("historical cases require a source case")
        return self


class DatasetManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(pattern=r"^phase12-[a-z0-9-]{3,80}$")
    code_version: str = Field(min_length=1, max_length=80)
    seed: int = Field(ge=0, le=2**31 - 1)
    case_count: int = Field(gt=0, le=10_000)
    split_counts: dict[SplitName, int]
    group_counts: dict[SplitName, int]
    case_digests: dict[str, str]
    dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: Annotated[tuple[DatasetCase, ...], BeforeValidator(_tuple_from_json)]

    @model_validator(mode="after")
    def validate_manifest(self) -> DatasetManifest:
        if self.case_count != len(self.cases) or len(self.case_digests) != self.case_count:
            raise ValueError("manifest case count does not match cases")
        if len({case.case_id for case in self.cases}) != self.case_count:
            raise ValueError("case ids must be unique")
        if any(self.split_counts.get(split, 0) < 0 for split in ("train", "dev", "test")):
            raise ValueError("split counts cannot be negative")
        return self


class CandidateVersion(StrictModel):
    schema_version: Literal["1"] = "1"
    candidate_id: str = Field(pattern=r"^phase12-(prompt|skill)-[a-z0-9-]{3,80}$")
    kind: CandidateKind
    parent_id: str = Field(min_length=1, max_length=120)
    source_case_ids: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_json)] = Field(
        min_length=1, max_length=10
    )
    content: str = Field(min_length=1, max_length=4_000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    boundary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["PENDING", "RETAINED", "REJECTED"] = "PENDING"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_content_digest(self) -> CandidateVersion:
        if digest_bytes(self.content.encode("utf-8")) != self.content_sha256:
            raise ValueError("candidate content digest mismatch")
        if len(set(self.source_case_ids)) != len(self.source_case_ids):
            raise ValueError("candidate sources must be unique")
        expected_prefix = "phase12-prompt-" if self.kind == "PROMPT" else "phase12-skill-"
        if not self.candidate_id.startswith(expected_prefix):
            raise ValueError("candidate id does not match kind")
        if self.candidate_id.rsplit("-", 1)[-1] != self.content_sha256[:16]:
            raise ValueError("candidate id digest suffix does not match content")
        return self


class ExperimentRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    experiment_id: str = Field(pattern=r"^phase12-exp-[a-z0-9-]{3,100}$")
    code_version: str = Field(min_length=1, max_length=80)
    dataset_id: str = Field(min_length=1, max_length=100)
    dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: SplitName
    method: str = Field(min_length=1, max_length=80)
    candidate_id: str | None = Field(
        default=None, pattern=r"^phase12-(prompt|skill)-[a-z0-9-]{3,80}$"
    )
    candidate_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_boundary_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reservation_key: str | None = Field(
        default=None, min_length=1, max_length=240, pattern=r"^[^\r\n]+$"
    )
    reservation_keys: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_json)] = Field(
        default_factory=tuple, max_length=8
    )
    model: str = Field(min_length=1, max_length=160)
    repeat: int = Field(ge=1, le=20)
    status: Literal["SUCCEEDED", "FAILED", "UNKNOWN", "REJECTED"]
    output_action: str | None = Field(default=None, max_length=80)
    verifier_passed: bool
    safety_passed: bool
    score: float = Field(ge=-100.0, le=100.0)
    failure_code: str | None = Field(default=None, max_length=120)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    elapsed_ms: float = Field(ge=0.0, le=3_600_000.0)
    calls: int = Field(ge=0, le=8)

    @model_validator(mode="after")
    def validate_status(self) -> ExperimentRecord:
        finite(self.score)
        if self.status == "SUCCEEDED" and not self.verifier_passed:
            raise ValueError("successful experiment must pass verifier")
        if self.status in {"FAILED", "UNKNOWN", "REJECTED"} and not self.failure_code:
            raise ValueError("non-successful experiment requires failure code")
        if self.candidate_id is None:
            if (
                self.candidate_content_sha256 is not None
                or self.candidate_boundary_sha256 is not None
            ):
                raise ValueError("candidate digests require candidate_id")
        elif self.candidate_content_sha256 is None or self.candidate_boundary_sha256 is None:
            raise ValueError("candidate experiments require content and boundary digests")
        if len(set(self.reservation_keys)) != len(self.reservation_keys):
            raise ValueError("reservation keys must be unique")
        if (
            self.reservation_key is not None
            and self.reservation_keys
            and self.reservation_key not in self.reservation_keys
        ):
            raise ValueError("primary reservation key must be included in reservation keys")
        return self


class TrainingArtifact(StrictModel):
    schema_version: Literal["1"] = "1"
    artifact_id: str = Field(pattern=r"^phase12-train-(sft|dpo|reinforce)-[a-z0-9-]{3,100}$")
    method: Literal["sft", "dpo", "reinforce"]
    code_version: str = Field(min_length=1, max_length=80)
    dataset_id: str = Field(min_length=1, max_length=100)
    dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(ge=0, le=2**31 - 1)
    feature_version: str = Field(min_length=1, max_length=40)
    action_version: str = Field(min_length=1, max_length=40)
    config: dict[str, int | float | str]
    initial_weight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    weight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_weight_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metrics: dict[str, float]
    checkpoint_metrics: Annotated[
        tuple[dict[str, float], ...], BeforeValidator(_tuple_from_json)
    ] = Field(default_factory=tuple, max_length=20)
    weight_path: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_metrics(self) -> TrainingArtifact:
        for value in self.metrics.values():
            finite(value)
        for checkpoint in self.checkpoint_metrics:
            for value in checkpoint.values():
                finite(value)
        if not self.weight_path.startswith("output/phase12/"):
            raise ValueError("training weights must remain in the Phase 12 output root")
        return self


class LabSelection(StrictModel):
    schema_version: Literal["1"] = "1"
    selection_id: str = Field(pattern=r"^phase12-selection-[a-z0-9-]{3,100}$")
    previous_id: str = Field(min_length=1, max_length=120)
    selected_id: str = Field(min_length=1, max_length=120)
    action: SelectionAction
    evidence_ids: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_json)] = Field(
        min_length=1, max_length=240
    )
    version_digests: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_version_digests(self) -> LabSelection:
        allowed = {self.previous_id, self.selected_id}
        if self.previous_id == self.selected_id:
            raise ValueError("selection must move between two different versions")
        if set(self.version_digests) != allowed:
            raise ValueError("selection must include both version digests")
        if not set(self.version_digests).issubset(allowed):
            raise ValueError("selection contains a digest for an unrelated version")
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in self.version_digests.values()
        ):
            raise ValueError("selection version digest is invalid")
        return self


class ActiveLabVersion(StrictModel):
    schema_version: Literal["1"] = "1"
    version_id: str = Field(min_length=1, max_length=120)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_id: str = Field(pattern=r"^phase12-selection-[a-z0-9-]{3,100}$")
    action: SelectionAction


def safe_output_path(root: Path, relative: str) -> Path:
    """Resolve a relative lab path and reject traversal or symlink escape."""
    if not relative or relative.startswith("/"):
        raise ValueError("output path must be relative")
    base = root.resolve()
    lexical = root / relative
    try:
        lexical.relative_to(root)
    except ValueError as error:
        raise ValueError("output path must be relative") from error
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("output path cannot traverse a symlink")
    target = lexical.resolve()
    try:
        target.relative_to(base)
    except ValueError as error:
        raise ValueError("output path escapes lab root") from error
    return target
