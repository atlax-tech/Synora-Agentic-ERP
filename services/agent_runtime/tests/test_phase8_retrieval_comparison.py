"""Same-dataset comparison harness tests for Phase 8 T05."""

from collections.abc import Sequence

from agent_runtime.evaluation.phase8_retrieval import (
    Phase8RetrievalDataset,
    load_phase8_retrieval_dataset,
)
from agent_runtime.evaluation.phase8_retrieval_compare import (
    _build_arm_report,
    _case_record,
    _validated_negative_expectations,
    load_phase8_retrieval_comparison_config,
    run_phase8_retrieval_comparison,
)
from agent_runtime.retrieval.chunks import SourceChunk, chunk_sources
from agent_runtime.retrieval.sources import CuratedSource
from agent_runtime.retrieval.vector_lab import VectorHit


def _fixed_chunks() -> tuple[Phase8RetrievalDataset, tuple[SourceChunk, ...]]:
    dataset = load_phase8_retrieval_dataset()
    return dataset, chunk_sources(
        tuple(CuratedSource(**source.model_dump()) for source in dataset.sources)
    )


class FixedEmbedding:
    model_id = "test-embedding"
    revision = "test-revision"

    @staticmethod
    def _category(text: str, *, query: bool) -> tuple[float, ...]:
        lowered = text.lower()
        if "purchase order" in lowered:
            return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        if "补货" in text:
            return (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        if "revision target" in lowered:
            return (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        if "erp version target" in lowered:
            return (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
        if "ignore system" in lowered:
            return (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        # Keep unknown queries orthogonal to unknown passages so the fixed
        # relevance gate can prove the unrelated/no-hit case.
        return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0 if query else 0.0, 0.0 if query else 1.0, 0.0)

    def encode(
        self,
        texts: Sequence[str],
        *,
        prefix: str,
    ) -> Sequence[Sequence[float]]:
        return tuple(self._category(text, query=prefix == "query") for text in texts)


class FixedReranker:
    model_id = "test-reranker"
    revision = "test-revision"

    def score(self, query: str, text: str) -> float:
        return 1.0 if query.lower() in text.lower() else 0.5


def test_comparison_uses_four_arms_and_same_fixed_dataset() -> None:
    first = run_phase8_retrieval_comparison(
        embedding_encoder=FixedEmbedding(),
        reranker=FixedReranker(),
    )
    second = run_phase8_retrieval_comparison(
        embedding_encoder=FixedEmbedding(),
        reranker=FixedReranker(),
    )

    assert first.case_count == 9
    assert tuple(arm.arm for arm in first.arms) == ("fts5", "vector", "hybrid", "rerank")
    assert all(arm.status == "PASS" for arm in first.arms)
    assert all(arm.case_count == 9 for arm in first.arms)
    assert all(arm.metadata_boundary_violation_count == 0 for arm in first.arms)
    assert all(arm.injection_boundary_violation_count == 0 for arm in first.arms)
    assert first.all_safety_passed is True
    assert first.adoption_decision == "KEEP_FTS5"
    assert first.deterministic_fingerprint == second.deterministic_fingerprint
    for arm in first.arms:
        negative = {record.case_id: record for record in arm.records if not record.expect_hit}
        assert negative["wrong-revision"].actual_chunk_ids == ()
        assert negative["wrong-erp-version"].actual_chunk_ids == ()
        assert negative["unrelated"].actual_chunk_ids == ()


def test_comparison_config_is_pinned_and_does_not_change_business_default() -> None:
    config = load_phase8_retrieval_comparison_config()

    assert config.dataset == "p8-retrieval-baseline.json"
    assert config.embedding_model.model_id == "intfloat/multilingual-e5-small"
    assert config.embedding_model.revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert config.reranker_model.model_id == "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    assert config.reranker_model.revision == "1427fd652930e4ba29e8149678df786c240d8825"
    assert config.vector_min_similarity == 0.8


def test_authorized_semantic_alternatives_are_not_permission_leakage() -> None:
    dataset, chunks = _fixed_chunks()
    config = load_phase8_retrieval_comparison_config()
    case = next(case for case in dataset.cases if case.case_id == "wrong-permission")
    internal = tuple(
        VectorHit.from_chunk(next(chunk for chunk in chunks if chunk.path == path), score=0.84)
        for path in ("normal.md", "cjk.md")
    )

    record = _case_record(
        case,
        internal,
        1.0,
        config.negative_expectations[case.case_id],
    )

    assert record.negative_expectation_mode == "FORBIDDEN_TARGET_ABSENT"
    assert record.negative_case_correct is True
    assert record.forbidden_target_hit_count == 0
    assert record.authorized_alternative_hit_count == 2
    assert record.metadata_boundary_violation_count == 0
    assert record.hit_at_k is True


def test_forbidden_target_disqualifies_an_arm_and_cannot_report_pass() -> None:
    dataset, chunks = _fixed_chunks()
    config = load_phase8_retrieval_comparison_config()
    case = next(case for case in dataset.cases if case.case_id == "wrong-permission")
    public_hit = VectorHit.from_chunk(
        next(chunk for chunk in chunks if chunk.path == "public.md"), score=0.95
    )
    record = _case_record(
        case,
        (public_hit,),
        1.0,
        config.negative_expectations[case.case_id],
    )

    report = _build_arm_report(
        arm="vector",
        status="PASS",
        model_id="test-embedding",
        model_revision="test-revision",
        records=(record,),
        build_latency_ms=0.0,
        peak_memory_bytes=0,
        index_size_bytes=0,
        model_size_bytes=0,
        dependency_footprint="test",
    )

    assert record.negative_case_correct is False
    assert record.forbidden_target_hit_count == 1
    assert report.status == "DISQUALIFIED"
    assert report.negative_case_correctness == 0.0


def test_no_hit_negative_policies_remain_strict() -> None:
    dataset, chunks = _fixed_chunks()
    config = load_phase8_retrieval_comparison_config()
    unrelated_hit = VectorHit.from_chunk(
        next(chunk for chunk in chunks if chunk.path == "normal.md"), score=0.84
    )

    for case_id in ("wrong-revision", "wrong-erp-version", "unrelated"):
        case = next(case for case in dataset.cases if case.case_id == case_id)
        record = _case_record(
            case,
            (unrelated_hit,),
            1.0,
            config.negative_expectations[case_id],
        )
        assert record.negative_expectation_mode == "NO_HIT"
        assert record.negative_case_correct is False


def test_negative_expectation_policy_covers_each_t04_negative_case() -> None:
    dataset, _ = _fixed_chunks()
    config = load_phase8_retrieval_comparison_config()

    policies = _validated_negative_expectations(dataset, config)

    assert set(policies) == {case.case_id for case in dataset.cases if not case.expect_hit}
