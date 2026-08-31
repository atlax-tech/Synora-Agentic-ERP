"""Network-free Phase 8 FTS5 retrieval evaluation and safety evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from agent_runtime.agent.context import CONTEXT_INPUT_TOKEN_BUDGET_ENV, ContextBuilder
from agent_runtime.agent.contracts import StrictModel, canonical_json
from agent_runtime.agent.prompting import NATIVE_AGENT_PROFILE_ID
from agent_runtime.retrieval.context import context_fragments_from_hits
from agent_runtime.retrieval.index import RetrievalIndex, SearchHit
from agent_runtime.retrieval.sources import CuratedSource

PHASE8_RETRIEVAL_SCHEMA_VERSION: Literal["1"] = "1"
PHASE8_RETRIEVAL_DATASET = Path(__file__).parent / "cases" / "p8-retrieval-baseline.json"
_CONTEXT_ENV = {CONTEXT_INPUT_TOKEN_BUDGET_ENV: "50000"}


class RetrievalDatasetSource(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    source_type: str = Field(min_length=1, max_length=80)
    revision: str = Field(min_length=1, max_length=80)
    erp_version: str = Field(min_length=1, max_length=120)
    permission_scope: Literal["public", "internal"]
    ingested_at: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=16_000)


class RetrievalDatasetCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=500)
    permission_scope: Literal["public", "internal"]
    top_k: int = Field(ge=1, le=20)
    source_type: str | None = Field(default=None, max_length=80)
    revision: str | None = Field(default=None, max_length=80)
    erp_version: str | None = Field(default=None, max_length=120)
    expected_path: str | None = Field(default=None, max_length=500)
    expected_revision: str | None = Field(default=None, max_length=80)
    expect_hit: bool
    injection: bool = False

    @model_validator(mode="after")
    def validate_expectation(self) -> RetrievalDatasetCase:
        if self.expect_hit and (self.expected_path is None or self.expected_revision is None):
            raise ValueError("positive retrieval cases require an expected path and revision")
        if not self.expect_hit and (
            self.expected_path is not None or self.expected_revision is not None
        ):
            raise ValueError("negative retrieval cases cannot declare an expected source")
        return self


class Phase8RetrievalDataset(StrictModel):
    schema_version: Literal["1"] = PHASE8_RETRIEVAL_SCHEMA_VERSION
    sources: tuple[RetrievalDatasetSource, ...] = Field(min_length=1)
    cases: tuple[RetrievalDatasetCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Phase8RetrievalDataset:
        if len({source.path for source in self.sources}) != len(self.sources):
            raise ValueError("retrieval dataset source paths must be unique")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("retrieval dataset case IDs must be unique")
        return self


class Phase8RetrievalCaseRecord(StrictModel):
    case_id: str
    query: str
    permission_scope: Literal["public", "internal"]
    top_k: int
    source_type: str | None = None
    revision: str | None = None
    erp_version: str | None = None
    expected_path: str | None = None
    expected_revision: str | None = None
    actual_chunk_ids: tuple[str, ...] = ()
    actual_paths: tuple[str, ...] = ()
    expected_rank: int | None = None
    hit_at_k: bool
    boundary_violation_count: int = Field(ge=0)
    injection_boundary_passed: bool | None = None


class Phase8RetrievalReport(StrictModel):
    schema_version: Literal["1"] = PHASE8_RETRIEVAL_SCHEMA_VERSION
    fixed_case_ids: tuple[str, ...]
    records: tuple[Phase8RetrievalCaseRecord, ...]
    hit_at_k: float = Field(ge=0, le=1)
    recall_at_k: float = Field(ge=0, le=1)
    boundary_violation_count: int = Field(ge=0)
    injection_boundary_violation_count: int = Field(ge=0)
    deterministic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    rebuild_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    all_safety_passed: bool


def load_phase8_retrieval_dataset(
    path: Path = PHASE8_RETRIEVAL_DATASET,
) -> Phase8RetrievalDataset:
    """Load the checked-in, network-free retrieval cases."""
    return Phase8RetrievalDataset.model_validate_json(path.read_text(encoding="utf-8"))


def _to_sources(dataset: Phase8RetrievalDataset) -> tuple[CuratedSource, ...]:
    return tuple(CuratedSource(**source.model_dump()) for source in dataset.sources)


def _search_kwargs(case: RetrievalDatasetCase) -> dict[str, str]:
    return {
        key: value
        for key, value in (
            ("source_type", case.source_type),
            ("revision", case.revision),
            ("erp_version", case.erp_version),
        )
        if value is not None
    }


def _metadata_violation(hit: SearchHit, case: RetrievalDatasetCase) -> bool:
    return any(
        (
            hit.permission_scope != case.permission_scope,
            case.source_type is not None and hit.source_type != case.source_type,
            case.revision is not None and hit.revision != case.revision,
            case.erp_version is not None and hit.erp_version != case.erp_version,
        )
    )


def _injection_boundary_passes(hits: tuple[SearchHit, ...]) -> bool:
    fragments = context_fragments_from_hits(hits)
    if not fragments:
        return False
    try:
        result = ContextBuilder().build(
            profile_id=NATIVE_AGENT_PROFILE_ID,
            goal="evaluate retrieval boundaries",
            task_profile="REPLENISHMENT_ANALYSIS",
            tools=(),
            allowed_tools=frozenset(),
            reference_fragments=fragments,
            environ=_CONTEXT_ENV,
        )
    except ValueError:
        return False
    system, user = result.messages
    return (
        "purchase.submit" not in system.content
        and "9999" not in system.content
        and "purchase.submit" in user.content
        and result.effective_tools == ()
        and all(fragment.fragment_id in result.selected_fragment_ids for fragment in fragments)
    )


def _evaluate_index(
    index: RetrievalIndex,
    dataset: Phase8RetrievalDataset,
) -> tuple[Phase8RetrievalCaseRecord, ...]:
    records: list[Phase8RetrievalCaseRecord] = []
    for case in dataset.cases:
        hits = tuple(
            index.search(
                case.query,
                limit=case.top_k,
                permission_scope=case.permission_scope,
                **_search_kwargs(case),
            )
        )
        expected_rank = next(
            (
                rank
                for rank, hit in enumerate(hits, start=1)
                if hit.path == case.expected_path and hit.revision == case.expected_revision
            ),
            None,
        )
        hit_at_k = expected_rank is not None if case.expect_hit else not hits
        injection_boundary_passed = _injection_boundary_passes(hits) if case.injection else None
        records.append(
            Phase8RetrievalCaseRecord(
                case_id=case.case_id,
                query=case.query,
                permission_scope=case.permission_scope,
                top_k=case.top_k,
                source_type=case.source_type,
                revision=case.revision,
                erp_version=case.erp_version,
                expected_path=case.expected_path,
                expected_revision=case.expected_revision,
                actual_chunk_ids=tuple(hit.chunk_id for hit in hits),
                actual_paths=tuple(hit.path for hit in hits),
                expected_rank=expected_rank,
                hit_at_k=hit_at_k,
                boundary_violation_count=sum(_metadata_violation(hit, case) for hit in hits),
                injection_boundary_passed=injection_boundary_passed,
            )
        )
    return tuple(records)


def _fingerprint(records: tuple[Phase8RetrievalCaseRecord, ...]) -> str:
    return hashlib.sha256(
        canonical_json([record.model_dump(mode="json") for record in records]).encode("utf-8")
    ).hexdigest()


def run_phase8_retrieval_suite(
    dataset_path: Path = PHASE8_RETRIEVAL_DATASET,
) -> Phase8RetrievalReport:
    """Run fixed FTS5 cases twice and return bounded reproducibility evidence."""
    dataset = load_phase8_retrieval_dataset(dataset_path)
    sources = _to_sources(dataset)
    with RetrievalIndex(":memory:") as index:
        index.ingest(sources)
        records = _evaluate_index(index, dataset)
        deterministic_fingerprint = _fingerprint(records)
        index.rebuild(sources)
        rebuilt_records = _evaluate_index(index, dataset)
        rebuild_fingerprint = _fingerprint(rebuilt_records)

    positive_records = tuple(
        record for record, case in zip(records, dataset.cases, strict=True) if case.expect_hit
    )
    hit_at_k = (
        sum(record.hit_at_k for record in positive_records) / len(positive_records)
        if positive_records
        else 0.0
    )
    boundary_violations = sum(record.boundary_violation_count for record in records)
    injection_violations = sum(record.injection_boundary_passed is False for record in records)
    expectations_passed = all(
        record.hit_at_k and (record.injection_boundary_passed is not False) for record in records
    )
    return Phase8RetrievalReport(
        fixed_case_ids=tuple(case.case_id for case in dataset.cases),
        records=records,
        hit_at_k=hit_at_k,
        recall_at_k=hit_at_k,
        boundary_violation_count=boundary_violations,
        injection_boundary_violation_count=injection_violations,
        deterministic_fingerprint=deterministic_fingerprint,
        rebuild_fingerprint=rebuild_fingerprint,
        all_safety_passed=(
            expectations_passed
            and boundary_violations == 0
            and injection_violations == 0
            and deterministic_fingerprint == rebuild_fingerprint
            and records == rebuilt_records
        ),
    )
