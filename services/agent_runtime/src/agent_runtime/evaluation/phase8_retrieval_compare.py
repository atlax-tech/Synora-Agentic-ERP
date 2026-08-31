"""Same-dataset FTS5/vector/hybrid/rerank comparison for Phase 8 T05.

The comparison is a LAB_ONLY measurement harness.  It never changes the
Runtime retrieval path, tool allowlist, ERP state, or Memory authority.
"""

from __future__ import annotations

import hashlib
import math
import os
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field

from agent_runtime.agent.context import CONTEXT_INPUT_TOKEN_BUDGET_ENV, ContextBuilder
from agent_runtime.agent.contracts import StrictModel, canonical_json
from agent_runtime.agent.prompting import NATIVE_AGENT_PROFILE_ID
from agent_runtime.evaluation.phase8_retrieval import (
    PHASE8_RETRIEVAL_DATASET,
    Phase8RetrievalDataset,
    RetrievalDatasetCase,
    load_phase8_retrieval_dataset,
    run_phase8_retrieval_suite,
)
from agent_runtime.retrieval.chunks import chunk_sources
from agent_runtime.retrieval.context import context_fragments_from_hits
from agent_runtime.retrieval.hybrid_lab import FusionInputHit, HybridHit, reciprocal_rank_fusion
from agent_runtime.retrieval.index import RetrievalIndex, SearchHit
from agent_runtime.retrieval.rerank_lab import (
    MAX_RERANK_CANDIDATES,
    Reranker,
    RerankerModelSpec,
    load_local_reranker,
    rerank_hits,
)
from agent_runtime.retrieval.sources import CuratedSource
from agent_runtime.retrieval.vector_lab import (
    EmbeddingEncoder,
    EmbeddingModelSpec,
    LocalVectorIndex,
    VectorLabError,
    VectorLabUnavailable,
    load_local_embedding_model,
)

PHASE8_RETRIEVAL_COMPARISON_CONFIG = (
    Path(__file__).parent / "cases" / "p8-retrieval-comparison.json"
)
_CONTEXT_ENV = {CONTEXT_INPUT_TOKEN_BUDGET_ENV: "50000"}

ArmName = Literal["fts5", "vector", "hybrid", "rerank"]
ArmStatus = Literal["PASS", "FAILED", "UNAVAILABLE", "DISQUALIFIED"]
NegativeExpectationMode = Literal["NO_HIT", "FORBIDDEN_TARGET_ABSENT"]


class ComparisonModelSpec(StrictModel):
    model_id: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=100)


class ComparisonDeterministicConfig(StrictModel):
    seed: int = Field(ge=0)
    threads: int = Field(ge=1, le=8)


class NegativeCaseExpectation(StrictModel):
    mode: NegativeExpectationMode
    forbidden_paths: tuple[str, ...] = ()

    @property
    def is_no_hit(self) -> bool:
        return self.mode == "NO_HIT"

    @property
    def is_forbidden_target_absent(self) -> bool:
        return self.mode == "FORBIDDEN_TARGET_ABSENT"


class RetrievalComparisonConfig(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset: str = Field(min_length=1, max_length=200)
    fts_top_k: int = Field(ge=1, le=20)
    vector_top_k: int = Field(ge=1, le=20)
    vector_min_similarity: float = Field(ge=-1, le=1)
    hybrid_top_k: int = Field(ge=1, le=20)
    rrf_k: int = Field(ge=1, le=10_000)
    rerank_candidate_pool: int = Field(ge=1, le=MAX_RERANK_CANDIDATES)
    rerank_top_k: int = Field(ge=1, le=20)
    embedding_model: ComparisonModelSpec
    reranker_model: ComparisonModelSpec
    negative_expectations: dict[str, NegativeCaseExpectation]
    deterministic: ComparisonDeterministicConfig

    @staticmethod
    def _validate_negative_expectation(case_id: str, expectation: NegativeCaseExpectation) -> None:
        if expectation.mode == "NO_HIT" and expectation.forbidden_paths:
            raise ValueError(f"NO_HIT case {case_id} cannot declare forbidden paths")
        if expectation.mode == "FORBIDDEN_TARGET_ABSENT" and not expectation.forbidden_paths:
            raise ValueError(f"FORBIDDEN_TARGET_ABSENT case {case_id} requires forbidden paths")


class ComparisonCaseRecord(StrictModel):
    case_id: str
    expect_hit: bool
    actual_chunk_ids: tuple[str, ...] = ()
    actual_paths: tuple[str, ...] = ()
    expected_rank: int | None = None
    hit_at_k: bool
    mrr_at_k: float = Field(ge=0, le=1)
    negative_expectation_mode: NegativeExpectationMode | None = None
    negative_case_correct: bool | None = None
    forbidden_target_hit_count: int = Field(ge=0)
    authorized_alternative_hit_count: int = Field(ge=0)
    metadata_boundary_violation_count: int = Field(ge=0)
    injection_boundary_passed: bool | None = None
    query_latency_ms: float = Field(ge=0)


class RetrievalArmReport(StrictModel):
    arm: ArmName
    status: ArmStatus
    model_id: str | None = None
    model_revision: str | None = None
    case_count: int = Field(ge=0)
    records: tuple[ComparisonCaseRecord, ...] = ()
    hit_at_k: float = Field(ge=0, le=1)
    recall_at_k: float = Field(ge=0, le=1)
    mrr_at_k: float = Field(ge=0, le=1)
    negative_case_correctness: float = Field(ge=0, le=1)
    metadata_boundary_violation_count: int = Field(ge=0)
    injection_boundary_violation_count: int = Field(ge=0)
    deterministic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_latency_ms: tuple[float, ...] = ()
    query_latency_median_ms: float = Field(ge=0)
    query_latency_p95_ms: float = Field(ge=0)
    build_latency_ms: float = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    index_size_bytes: int = Field(ge=0)
    model_size_bytes: int = Field(ge=0)
    dependency_footprint: str = Field(min_length=1, max_length=200)
    error: str | None = Field(default=None, max_length=240)


class Phase8RetrievalComparisonReport(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset: str
    case_count: int = Field(ge=1)
    arms: tuple[RetrievalArmReport, ...]
    deterministic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    all_safety_passed: bool
    business_default: Literal["FTS5"] = "FTS5"
    adoption_decision: Literal["KEEP_FTS5", "CANDIDATE_FOR_ADOPTION"]


def load_phase8_retrieval_comparison_config(
    path: Path = PHASE8_RETRIEVAL_COMPARISON_CONFIG,
) -> RetrievalComparisonConfig:
    """Load only comparison knobs; source/cases remain owned by T04."""
    return RetrievalComparisonConfig.model_validate_json(path.read_text(encoding="utf-8"))


def _validated_negative_expectations(
    dataset: Phase8RetrievalDataset,
    config: RetrievalComparisonConfig,
) -> Mapping[str, NegativeCaseExpectation]:
    """Validate the T05-only negative policy against the immutable T04 corpus."""
    negative_cases = {case.case_id: case for case in dataset.cases if not case.expect_hit}
    configured_ids = set(config.negative_expectations)
    if configured_ids != set(negative_cases):
        raise ValueError(
            "comparison negative expectations must cover each T04 negative case exactly once"
        )
    source_scopes = {source.path: source.permission_scope for source in dataset.sources}
    for case_id, case in negative_cases.items():
        expectation = config.negative_expectations[case_id]
        RetrievalComparisonConfig._validate_negative_expectation(case_id, expectation)
        if len(set(expectation.forbidden_paths)) != len(expectation.forbidden_paths):
            raise ValueError(f"negative case {case_id} has duplicate forbidden paths")
        missing = set(expectation.forbidden_paths) - set(source_scopes)
        if missing:
            raise ValueError(f"negative case {case_id} references unknown forbidden paths")
        if expectation.mode == "FORBIDDEN_TARGET_ABSENT" and any(
            source_scopes[path] == case.permission_scope for path in expectation.forbidden_paths
        ):
            raise ValueError(
                f"negative case {case_id} forbidden target must be outside "
                "requested permission scope"
            )
    return config.negative_expectations


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


type ComparisonHit = FusionInputHit | HybridHit


def _as_search_hit(hit: ComparisonHit) -> SearchHit:
    return SearchHit(
        title=hit.title,
        path=hit.path,
        source_type=hit.source_type,
        revision=hit.revision,
        erp_version=hit.erp_version,
        permission_scope=hit.permission_scope,
        ingested_at=hit.ingested_at,
        score=hit.score,
        snippet=hit.snippet,
        chunk_id=hit.chunk_id,
        ordinal=hit.ordinal,
        section=hit.section,
        content_digest=hit.content_digest,
        content=hit.content,
    )


def _metadata_violation(hit: ComparisonHit, case: RetrievalDatasetCase) -> bool:
    expected_digest = hashlib.sha256(hit.content.encode("utf-8")).hexdigest()
    return any(
        (
            hit.permission_scope != case.permission_scope,
            case.source_type is not None and hit.source_type != case.source_type,
            case.revision is not None and hit.revision != case.revision,
            case.erp_version is not None and hit.erp_version != case.erp_version,
            hit.content_digest != expected_digest,
        )
    )


def _injection_boundary_passes(hits: Sequence[ComparisonHit]) -> bool:
    fragments = context_fragments_from_hits(tuple(_as_search_hit(hit) for hit in hits))
    if not fragments:
        return False
    try:
        result = ContextBuilder().build(
            profile_id=NATIVE_AGENT_PROFILE_ID,
            goal="evaluate retrieval comparison boundaries",
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


def _rss_bytes() -> int:
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except ImportError, AttributeError, ValueError:
        return 0
    return value if sys.platform == "darwin" else value * 1024


def _model_size_bytes(model_id: str) -> int:
    """Return a bounded cache footprint without recording local paths."""
    root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    model_dir = root / f"models--{model_id.replace('/', '--')}"
    if not model_dir.is_dir():
        return 0
    total = 0
    for path in model_dir.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def _case_record(
    case: RetrievalDatasetCase,
    hits: Sequence[ComparisonHit],
    query_latency_ms: float,
    negative_expectation: NegativeCaseExpectation | None,
) -> ComparisonCaseRecord:
    expected_rank = next(
        (
            rank
            for rank, hit in enumerate(hits, start=1)
            if hit.path == case.expected_path and hit.revision == case.expected_revision
        ),
        None,
    )
    metadata_boundary_violation_count = sum(_metadata_violation(hit, case) for hit in hits)
    forbidden_target_hit_count = (
        sum(hit.path in negative_expectation.forbidden_paths for hit in hits)
        if negative_expectation is not None
        else 0
    )
    authorized_alternative_hit_count = (
        sum(
            hit.permission_scope == case.permission_scope
            and hit.path
            not in (negative_expectation.forbidden_paths if negative_expectation else ())
            for hit in hits
        )
        if not case.expect_hit
        else 0
    )
    if case.expect_hit:
        hit_at_k = expected_rank is not None
        negative_case_correct = None
        negative_expectation_mode = None
    else:
        if negative_expectation is None:
            raise ValueError(f"missing negative expectation for {case.case_id}")
        if negative_expectation.mode == "NO_HIT":
            negative_case_correct = not hits
        else:
            negative_case_correct = (
                forbidden_target_hit_count == 0
                and metadata_boundary_violation_count == 0
                and all(hit.permission_scope == case.permission_scope for hit in hits)
            )
        # For negative cases this field means the configured expectation was
        # satisfied; it must not mislabel authorized semantic alternatives as
        # a retrieval hit or a lexical no-hit.
        hit_at_k = negative_case_correct
        negative_expectation_mode = negative_expectation.mode
    return ComparisonCaseRecord(
        case_id=case.case_id,
        expect_hit=case.expect_hit,
        actual_chunk_ids=tuple(hit.chunk_id for hit in hits),
        actual_paths=tuple(hit.path for hit in hits),
        expected_rank=expected_rank,
        hit_at_k=hit_at_k,
        mrr_at_k=(1 / expected_rank if expected_rank is not None else 0.0),
        negative_expectation_mode=negative_expectation_mode,
        negative_case_correct=negative_case_correct,
        forbidden_target_hit_count=forbidden_target_hit_count,
        authorized_alternative_hit_count=authorized_alternative_hit_count,
        metadata_boundary_violation_count=metadata_boundary_violation_count,
        injection_boundary_passed=(_injection_boundary_passes(hits) if case.injection else None),
        query_latency_ms=query_latency_ms,
    )


def _fingerprint(records: Sequence[ComparisonCaseRecord]) -> str:
    stable_records = [
        {
            "case_id": record.case_id,
            "expect_hit": record.expect_hit,
            "actual_chunk_ids": record.actual_chunk_ids,
            "actual_paths": record.actual_paths,
            "expected_rank": record.expected_rank,
            "hit_at_k": record.hit_at_k,
            "mrr_at_k": record.mrr_at_k,
            "negative_expectation_mode": record.negative_expectation_mode,
            "negative_case_correct": record.negative_case_correct,
            "forbidden_target_hit_count": record.forbidden_target_hit_count,
            "authorized_alternative_hit_count": record.authorized_alternative_hit_count,
            "metadata_boundary_violation_count": record.metadata_boundary_violation_count,
            "injection_boundary_passed": record.injection_boundary_passed,
        }
        for record in records
    ]
    return hashlib.sha256(canonical_json(stable_records).encode("utf-8")).hexdigest()


def _build_arm_report(
    *,
    arm: ArmName,
    status: ArmStatus,
    model_id: str | None,
    model_revision: str | None,
    records: Sequence[ComparisonCaseRecord],
    build_latency_ms: float,
    peak_memory_bytes: int,
    index_size_bytes: int,
    model_size_bytes: int,
    dependency_footprint: str,
    error: str | None = None,
) -> RetrievalArmReport:
    record_tuple = tuple(records)
    # The fixed dataset has both positive and negative cases; determine the
    # denominator from explicit case expectations instead of treating no-hit
    # cases as retrieval quality failures.
    positive_records = tuple(record for record in record_tuple if record.expect_hit)
    negative = tuple(record for record in record_tuple if record.negative_case_correct is not None)
    latencies = tuple(record.query_latency_ms for record in record_tuple)
    metadata_violations = sum(record.metadata_boundary_violation_count for record in record_tuple)
    injection_violations = sum(record.injection_boundary_passed is False for record in record_tuple)
    computed_status = status
    negative_failures = any(record.negative_case_correct is False for record in record_tuple)
    if status == "PASS" and (metadata_violations or injection_violations or negative_failures):
        computed_status = "DISQUALIFIED"
    fingerprint = _fingerprint(record_tuple)
    return RetrievalArmReport(
        arm=arm,
        status=computed_status,
        model_id=model_id,
        model_revision=model_revision,
        case_count=len(record_tuple),
        records=record_tuple,
        hit_at_k=(sum(record.hit_at_k for record in positive_records) / len(positive_records))
        if positive_records
        else 0.0,
        recall_at_k=(sum(record.hit_at_k for record in positive_records) / len(positive_records))
        if positive_records
        else 0.0,
        mrr_at_k=(sum(record.mrr_at_k for record in positive_records) / len(positive_records))
        if positive_records
        else 0.0,
        negative_case_correctness=(
            sum(bool(record.negative_case_correct) for record in negative) / len(negative)
            if negative
            else 0.0
        ),
        metadata_boundary_violation_count=metadata_violations,
        injection_boundary_violation_count=injection_violations,
        deterministic_fingerprint=fingerprint,
        query_latency_ms=latencies,
        query_latency_median_ms=statistics.median(latencies) if latencies else 0.0,
        query_latency_p95_ms=_p95(latencies),
        build_latency_ms=max(0.0, build_latency_ms),
        peak_memory_bytes=max(0, peak_memory_bytes),
        index_size_bytes=max(0, index_size_bytes),
        model_size_bytes=max(0, model_size_bytes),
        dependency_footprint=dependency_footprint,
        error=error,
    )


Runner = Callable[[RetrievalDatasetCase], Sequence[ComparisonHit]]


def _run_arm(
    *,
    arm: ArmName,
    dataset: Phase8RetrievalDataset,
    runner: Runner | None,
    model_id: str | None,
    model_revision: str | None,
    build_latency_ms: float,
    peak_memory_before: int,
    index_size_bytes: int,
    model_size_bytes: int,
    dependency_footprint: str,
    negative_expectations: Mapping[str, NegativeCaseExpectation],
    unavailable_error: str | None = None,
) -> RetrievalArmReport:
    if runner is None:
        return _build_arm_report(
            arm=arm,
            status="UNAVAILABLE",
            model_id=model_id,
            model_revision=model_revision,
            records=(),
            build_latency_ms=build_latency_ms,
            peak_memory_bytes=max(0, _rss_bytes() - peak_memory_before),
            index_size_bytes=index_size_bytes,
            model_size_bytes=model_size_bytes,
            dependency_footprint=dependency_footprint,
            error=unavailable_error or "experiment arm unavailable",
        )
    records: list[ComparisonCaseRecord] = []
    try:
        for case in dataset.cases:
            started = time.perf_counter()
            hits = tuple(runner(case))
            latency_ms = (time.perf_counter() - started) * 1000
            records.append(
                _case_record(case, hits, latency_ms, negative_expectations.get(case.case_id))
            )
    except (VectorLabError, VectorLabUnavailable) as exc:
        return _build_arm_report(
            arm=arm,
            status="FAILED",
            model_id=model_id,
            model_revision=model_revision,
            records=records,
            build_latency_ms=build_latency_ms,
            peak_memory_bytes=max(0, _rss_bytes() - peak_memory_before),
            index_size_bytes=index_size_bytes,
            model_size_bytes=model_size_bytes,
            dependency_footprint=dependency_footprint,
            error=str(exc),
        )
    except Exception:
        return _build_arm_report(
            arm=arm,
            status="FAILED",
            model_id=model_id,
            model_revision=model_revision,
            records=records,
            build_latency_ms=build_latency_ms,
            peak_memory_bytes=max(0, _rss_bytes() - peak_memory_before),
            index_size_bytes=index_size_bytes,
            model_size_bytes=model_size_bytes,
            dependency_footprint=dependency_footprint,
            error="comparison arm failed",
        )
    return _build_arm_report(
        arm=arm,
        status="PASS",
        model_id=model_id,
        model_revision=model_revision,
        records=records,
        build_latency_ms=build_latency_ms,
        peak_memory_bytes=max(0, _rss_bytes() - peak_memory_before),
        index_size_bytes=index_size_bytes,
        model_size_bytes=model_size_bytes,
        dependency_footprint=dependency_footprint,
    )


def _fts_runner(
    index: RetrievalIndex,
    case: RetrievalDatasetCase,
    *,
    limit: int,
) -> Sequence[SearchHit]:
    return index.search(
        case.query,
        limit=limit,
        permission_scope=case.permission_scope,
        **_search_kwargs(case),
    )


def run_phase8_retrieval_comparison(
    dataset_path: Path = PHASE8_RETRIEVAL_DATASET,
    config_path: Path = PHASE8_RETRIEVAL_COMPARISON_CONFIG,
    *,
    embedding_encoder: EmbeddingEncoder | None = None,
    reranker: Reranker | None = None,
) -> Phase8RetrievalComparisonReport:
    """Run all four arms on the T04 dataset and return an adoption package."""
    config = load_phase8_retrieval_comparison_config(config_path)
    if Path(config.dataset).name != dataset_path.name:
        raise ValueError("comparison must use the T04 fixed dataset")
    dataset = load_phase8_retrieval_dataset(dataset_path)
    negative_expectations = _validated_negative_expectations(dataset, config)
    sources = _to_sources(dataset)
    chunks = chunk_sources(sources)

    with RetrievalIndex(":memory:") as fts_index:
        fts_started = time.perf_counter()
        fts_index.ingest(sources)
        fts_build_ms = (time.perf_counter() - fts_started) * 1000
        fts_arm = _run_arm(
            arm="fts5",
            dataset=dataset,
            runner=lambda case: _fts_runner(fts_index, case, limit=config.fts_top_k),
            model_id="sqlite-fts5",
            model_revision="t04-ac05a70",
            build_latency_ms=fts_build_ms,
            peak_memory_before=_rss_bytes(),
            index_size_bytes=0,
            model_size_bytes=0,
            dependency_footprint="stdlib sqlite3 / existing Runtime baseline",
            negative_expectations=negative_expectations,
        )

        vector_index: LocalVectorIndex | None = None
        vector_error: str | None = None
        vector_before = _rss_bytes()
        vector_started = time.perf_counter()
        try:
            encoder = embedding_encoder or load_local_embedding_model(
                EmbeddingModelSpec(
                    model_id=config.embedding_model.model_id,
                    revision=config.embedding_model.revision,
                )
            )
            vector_index = LocalVectorIndex(encoder, min_similarity=config.vector_min_similarity)
            vector_index.build(chunks)
        except (VectorLabError, VectorLabUnavailable) as exc:
            vector_error = str(exc)
        vector_build_ms = (time.perf_counter() - vector_started) * 1000
        vector_model_id = (
            vector_index.model_id if vector_index is not None else config.embedding_model.model_id
        )
        vector_revision = (
            vector_index.model_revision
            if vector_index is not None
            else config.embedding_model.revision
        )
        vector_size = vector_index.index_size_bytes if vector_index is not None else 0
        vector_arm = _run_arm(
            arm="vector",
            dataset=dataset,
            runner=(
                (
                    lambda case: vector_index.search(
                        case.query,
                        limit=config.vector_top_k,
                        permission_scope=case.permission_scope,
                        **_search_kwargs(case),
                    )
                )
                if vector_index is not None
                else None
            ),
            model_id=vector_model_id,
            model_revision=vector_revision,
            build_latency_ms=vector_build_ms,
            peak_memory_before=vector_before,
            index_size_bytes=vector_size,
            model_size_bytes=_model_size_bytes(vector_model_id),
            dependency_footprint="optional retrieval-lab sentence-transformers / torch",
            negative_expectations=negative_expectations,
            unavailable_error=vector_error,
        )

        hybrid_runner: Runner | None = None
        hybrid_error: str | None = vector_error
        if vector_index is not None:

            def run_hybrid(case: RetrievalDatasetCase) -> Sequence[HybridHit]:
                return reciprocal_rank_fusion(
                    _fts_runner(fts_index, case, limit=config.fts_top_k),
                    vector_index.search(
                        case.query,
                        limit=max(config.vector_top_k, config.rerank_candidate_pool),
                        permission_scope=case.permission_scope,
                        **_search_kwargs(case),
                    ),
                    top_k=config.hybrid_top_k,
                    rrf_k=config.rrf_k,
                    permission_scope=case.permission_scope,
                    **_search_kwargs(case),
                )

            hybrid_runner = run_hybrid
        hybrid_arm = _run_arm(
            arm="hybrid",
            dataset=dataset,
            runner=hybrid_runner,
            model_id=f"{vector_model_id}+sqlite-fts5" if vector_index else None,
            model_revision=vector_revision if vector_index else None,
            build_latency_ms=fts_build_ms + vector_build_ms,
            peak_memory_before=vector_before,
            index_size_bytes=vector_size,
            model_size_bytes=_model_size_bytes(vector_model_id),
            dependency_footprint="optional retrieval-lab vector + existing sqlite3",
            negative_expectations=negative_expectations,
            unavailable_error=hybrid_error or "vector arm unavailable",
        )

        rerank_runner: Runner | None = None
        rerank_error: str | None = vector_error
        rerank_model_id: str | None = config.reranker_model.model_id
        rerank_revision: str | None = config.reranker_model.revision
        rerank_model_size = _model_size_bytes(config.reranker_model.model_id)
        rerank_started = time.perf_counter()
        if vector_index is not None:
            try:
                local_reranker = reranker or load_local_reranker(
                    RerankerModelSpec(
                        model_id=config.reranker_model.model_id,
                        revision=config.reranker_model.revision,
                    )
                )
                rerank_model_id = local_reranker.model_id
                rerank_revision = local_reranker.revision
                rerank_model_size = _model_size_bytes(config.reranker_model.model_id)

                def run_rerank(case: RetrievalDatasetCase) -> Sequence[HybridHit]:
                    candidates = reciprocal_rank_fusion(
                        _fts_runner(fts_index, case, limit=config.fts_top_k),
                        vector_index.search(
                            case.query,
                            limit=config.rerank_candidate_pool,
                            permission_scope=case.permission_scope,
                            **_search_kwargs(case),
                        ),
                        top_k=config.rerank_candidate_pool,
                        rrf_k=config.rrf_k,
                        permission_scope=case.permission_scope,
                        **_search_kwargs(case),
                    )
                    return rerank_hits(
                        case.query,
                        candidates,
                        local_reranker,
                        candidate_pool=config.rerank_candidate_pool,
                        top_k=config.rerank_top_k,
                    )

                rerank_runner = run_rerank
            except (VectorLabError, VectorLabUnavailable) as exc:
                rerank_error = str(exc)
        rerank_build_ms = (time.perf_counter() - rerank_started) * 1000
        rerank_arm = _run_arm(
            arm="rerank",
            dataset=dataset,
            runner=rerank_runner,
            model_id=rerank_model_id,
            model_revision=rerank_revision,
            build_latency_ms=fts_build_ms + vector_build_ms + rerank_build_ms,
            peak_memory_before=vector_before,
            index_size_bytes=vector_size,
            model_size_bytes=rerank_model_size,
            dependency_footprint="optional retrieval-lab vector + local cross-encoder",
            negative_expectations=negative_expectations,
            unavailable_error=rerank_error or "reranker arm unavailable",
        )

    arms = (fts_arm, vector_arm, hybrid_arm, rerank_arm)
    t04_report = run_phase8_retrieval_suite(dataset_path)
    fts_matches_t04 = tuple(record.actual_chunk_ids for record in fts_arm.records) == tuple(
        record.actual_chunk_ids for record in t04_report.records
    )
    comparison_fingerprint = hashlib.sha256(
        canonical_json(
            [
                {
                    "arm": arm.arm,
                    "status": arm.status,
                    "model_id": arm.model_id,
                    "model_revision": arm.model_revision,
                    "fingerprint": arm.deterministic_fingerprint,
                }
                for arm in arms
            ]
        ).encode("utf-8")
    ).hexdigest()
    all_safety_passed = fts_matches_t04 and all(
        arm.status == "PASS"
        and arm.case_count == len(dataset.cases)
        and arm.negative_case_correctness == 1.0
        and arm.metadata_boundary_violation_count == 0
        and arm.injection_boundary_violation_count == 0
        and bool(arm.deterministic_fingerprint)
        for arm in arms
    )
    adoption_decision: Literal["KEEP_FTS5", "CANDIDATE_FOR_ADOPTION"] = "KEEP_FTS5"
    if all_safety_passed and any(arm.hit_at_k > fts_arm.hit_at_k for arm in arms[1:]):
        adoption_decision = "CANDIDATE_FOR_ADOPTION"
    return Phase8RetrievalComparisonReport(
        dataset=dataset_path.name,
        case_count=len(dataset.cases),
        arms=arms,
        deterministic_fingerprint=comparison_fingerprint,
        all_safety_passed=all_safety_passed,
        adoption_decision=adoption_decision,
    )


__all__ = [
    "PHASE8_RETRIEVAL_COMPARISON_CONFIG",
    "Phase8RetrievalComparisonReport",
    "RetrievalArmReport",
    "RetrievalComparisonConfig",
    "run_phase8_retrieval_comparison",
]
