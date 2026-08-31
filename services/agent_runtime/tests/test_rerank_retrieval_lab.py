"""Bounded local reranker invariants for Phase 8 T05."""

import hashlib
import math

import pytest
from agent_runtime.retrieval.hybrid_lab import HybridHit
from agent_runtime.retrieval.rerank_lab import MAX_RERANK_CANDIDATES, rerank_hits
from agent_runtime.retrieval.vector_lab import VectorLabError


def _candidate(path: str, score: float) -> HybridHit:
    content = f"hybrid content {path}"
    return HybridHit(
        title=path,
        path=path,
        source_type="sop",
        revision="v1",
        erp_version="erp-a",
        permission_scope="internal",
        ingested_at="2026-08-31T00:00:00+00:00",
        score=score,
        snippet=content,
        chunk_id=hashlib.sha256(path.encode()).hexdigest(),
        ordinal=1,
        section="Test",
        content_digest=hashlib.sha256(content.encode()).hexdigest(),
        content=content,
        fts_rank=1,
        vector_rank=1,
    )


class FakeReranker:
    model_id = "test-reranker"
    revision = "test-revision"

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls: list[str] = []

    def score(self, query: str, text: str) -> float:
        self.calls.append(text)
        return self.scores[text]


def test_rerank_only_scores_bounded_candidates_and_preserves_citation_fields() -> None:
    candidates = tuple(_candidate(f"{index}.md", float(index)) for index in range(12))
    reranker = FakeReranker(
        {candidate.content: float(100 - index) for index, candidate in enumerate(candidates)}
    )

    ranked = rerank_hits(
        "query",
        candidates,
        reranker,
        candidate_pool=MAX_RERANK_CANDIDATES,
        top_k=5,
    )

    assert len(reranker.calls) == MAX_RERANK_CANDIDATES
    assert len(ranked) == 5
    assert all(hit.path in {f"{index}.md" for index in range(10)} for hit in ranked)
    for hit in ranked:
        original = next(candidate for candidate in candidates if candidate.chunk_id == hit.chunk_id)
        assert hit.content == original.content
        assert hit.content_digest == original.content_digest
        assert hit.fts_rank == original.fts_rank
        assert hit.vector_rank == original.vector_rank


def test_rerank_tie_break_is_chunk_id_and_invalid_bounds_are_fail_closed() -> None:
    candidates = (_candidate("a.md", 1.0), _candidate("b.md", 1.0))
    reranker = FakeReranker({candidate.content: 1.0 for candidate in candidates})

    first = rerank_hits("query", candidates, reranker, candidate_pool=2, top_k=2)
    second = rerank_hits("query", candidates, reranker, candidate_pool=2, top_k=2)
    assert tuple(hit.chunk_id for hit in first) == tuple(sorted(hit.chunk_id for hit in candidates))
    assert first == second
    assert rerank_hits("", candidates, reranker) == ()
    assert (
        rerank_hits("query", candidates, reranker, candidate_pool=MAX_RERANK_CANDIDATES + 1) == ()
    )


def test_rerank_non_finite_scores_raise_instead_of_reporting_success() -> None:
    candidate = _candidate("bad.md", 1.0)
    reranker = FakeReranker({candidate.content: math.nan})

    with pytest.raises(VectorLabError, match="non-finite"):
        rerank_hits("query", (candidate,), reranker)
