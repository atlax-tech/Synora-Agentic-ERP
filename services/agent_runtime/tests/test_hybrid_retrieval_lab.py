"""Hybrid RRF invariants for Phase 8 T05."""

import hashlib

from agent_runtime.retrieval.hybrid_lab import reciprocal_rank_fusion
from agent_runtime.retrieval.index import SearchHit
from agent_runtime.retrieval.vector_lab import VectorHit


def _content(path: str) -> tuple[str, str, str]:
    content = f"retrieval content for {path}"
    return (
        content,
        hashlib.sha256(content.encode()).hexdigest(),
        hashlib.sha256(path.encode()).hexdigest(),
    )


def _search_hit(
    path: str,
    score: float,
    *,
    permission_scope: str = "internal",
    revision: str = "v1",
) -> SearchHit:
    content, digest, chunk_id = _content(path)
    return SearchHit(
        title=path,
        path=path,
        source_type="sop",
        revision=revision,
        erp_version="erp-a",
        permission_scope=permission_scope,
        ingested_at="2026-08-31T00:00:00+00:00",
        score=score,
        snippet=content,
        chunk_id=chunk_id,
        ordinal=1,
        section="Test",
        content_digest=digest,
        content=content,
    )


def _vector_hit(
    path: str,
    score: float,
    *,
    permission_scope: str = "internal",
    revision: str = "v1",
) -> VectorHit:
    content, digest, chunk_id = _content(path)
    return VectorHit(
        title=path,
        path=path,
        source_type="sop",
        revision=revision,
        erp_version="erp-a",
        permission_scope=permission_scope,
        ingested_at="2026-08-31T00:00:00+00:00",
        score=score,
        snippet=content,
        chunk_id=chunk_id,
        ordinal=1,
        section="Test",
        content_digest=digest,
        content=content,
    )


def test_rrf_is_deterministic_and_does_not_add_raw_score_scales() -> None:
    fts = (_search_hit("a.md", -0.01), _search_hit("b.md", -1000.0))
    vector = (_vector_hit("b.md", 0.99), _vector_hit("a.md", 0.01))

    first = reciprocal_rank_fusion(fts, vector, top_k=2, rrf_k=60)
    second = reciprocal_rank_fusion(fts, vector, top_k=2, rrf_k=60)

    assert first == second
    assert {hit.path for hit in first} == {"a.md", "b.md"}
    assert all(hit.score <= 2 / 61 for hit in first)
    by_path = {hit.path: hit for hit in first}
    assert by_path["a.md"].fts_rank == 1
    assert by_path["a.md"].vector_rank == 2


def test_rrf_deduplicates_first_rank_and_bounds_results() -> None:
    a = _search_hit("a.md", 1.0)
    b = _search_hit("b.md", 1.0)
    c = _search_hit("c.md", 1.0)

    fused = reciprocal_rank_fusion((a, a, b, c), (), top_k=2)

    assert len(fused) == 2
    assert fused[0].chunk_id == a.chunk_id
    assert fused[0].fts_rank == 1
    assert fused[1].chunk_id == b.chunk_id
    assert reciprocal_rank_fusion((), (), top_k=5) == ()


def test_rrf_filters_scope_and_rejects_conflicting_citation_metadata() -> None:
    internal = _search_hit("same.md", 1.0)
    public = _vector_hit("same.md", 1.0, permission_scope="public")
    conflicting = _vector_hit("same.md", 1.0, revision="v2")

    public_filtered = reciprocal_rank_fusion((internal,), (public,), permission_scope="internal")
    assert len(public_filtered) == 1
    assert public_filtered[0].path == "same.md"
    assert public_filtered[0].vector_rank is None
    assert reciprocal_rank_fusion((internal,), (conflicting,), permission_scope="internal") == ()
