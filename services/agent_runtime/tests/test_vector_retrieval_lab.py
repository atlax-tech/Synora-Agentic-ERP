"""Local vector retrieval invariants for Phase 8 T05."""

from collections.abc import Sequence

import pytest
from agent_runtime.retrieval.chunks import chunk_sources
from agent_runtime.retrieval.sources import CuratedSource
from agent_runtime.retrieval.vector_lab import (
    VECTOR_MIN_COSINE,
    LocalVectorIndex,
    VectorLabError,
)


class FakeEmbedding:
    model_id = "test-embedding"
    revision = "test-revision"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def encode(
        self,
        texts: Sequence[str],
        *,
        prefix: str,
    ) -> Sequence[Sequence[float]]:
        self.calls.append((prefix, *texts))
        values: list[tuple[float, float]] = []
        for text in texts:
            lowered = text.lower()
            if "alpha" in lowered:
                values.append((1.0, 0.0))
            elif "beta" in lowered:
                values.append((0.0, 1.0))
            else:
                values.append((0.6, 0.8))
        return values


class BadDimensionEmbedding(FakeEmbedding):
    def encode(
        self,
        texts: Sequence[str],
        *,
        prefix: str,
    ) -> Sequence[Sequence[float]]:
        self.calls.append((prefix, *texts))
        return tuple((1.0, 0.0) if index == 0 else (1.0,) for index, _ in enumerate(texts))


class FailingEmbedding(FakeEmbedding):
    def encode(
        self,
        texts: Sequence[str],
        *,
        prefix: str,
    ) -> Sequence[Sequence[float]]:
        raise RuntimeError("network inference must never be used")


def _sources() -> tuple[CuratedSource, ...]:
    return (
        CuratedSource(
            source_type="sop",
            path="alpha.md",
            revision="v1",
            erp_version="erp-a",
            permission_scope="internal",
            ingested_at="2026-08-31T00:00:00+00:00",
            title="Alpha",
            content="## Procedure\nalpha procurement guidance",
        ),
        CuratedSource(
            source_type="sop",
            path="beta.md",
            revision="v2",
            erp_version="erp-b",
            permission_scope="internal",
            ingested_at="2026-08-31T00:00:00+00:00",
            title="Beta",
            content="## Procedure\nbeta procurement guidance",
        ),
    )


def test_vector_index_reuses_chunks_and_applies_metadata_before_return() -> None:
    encoder = FakeEmbedding()
    chunks = chunk_sources(_sources())
    index = LocalVectorIndex(encoder)

    assert index.build(chunks) == len(chunks)
    hits = index.search("alpha", permission_scope="internal", source_type="sop")

    assert hits[0].path == "alpha.md"
    assert hits[0].chunk_id in {chunk.chunk_id for chunk in chunks}
    assert hits[0].content_digest
    assert index.search("alpha", permission_scope="internal", revision="v9") == []
    assert index.search("alpha", permission_scope="public") == []
    assert encoder.calls[0][0] == "passage"
    assert encoder.calls[-1][0] == "query"


def test_vector_ranking_is_deterministic_and_same_chunks_keep_identity() -> None:
    chunks = chunk_sources(_sources())
    first = LocalVectorIndex(FakeEmbedding())
    second = LocalVectorIndex(FakeEmbedding())
    first.build(chunks)
    second.build(tuple(reversed(chunks)))

    first_ids = tuple(hit.chunk_id for hit in first.search("unrelated"))
    second_ids = tuple(hit.chunk_id for hit in second.search("unrelated"))
    assert first_ids == second_ids
    assert first.index_size_bytes == second.index_size_bytes


def test_vector_similarity_gate_rejects_low_confidence_queries() -> None:
    class LowScore(FakeEmbedding):
        def encode(
            self,
            texts: Sequence[str],
            *,
            prefix: str,
        ) -> Sequence[Sequence[float]]:
            self.calls.append((prefix, *texts))
            return tuple((0.0, 1.0) if prefix == "query" else (1.0, 0.0) for _ in texts)

    index = LocalVectorIndex(LowScore())
    index.build(chunk_sources(_sources()))
    assert VECTOR_MIN_COSINE == 0.8
    assert index.search("anything") == []


def test_vector_embedding_dimension_and_inference_errors_fail_closed() -> None:
    with pytest.raises(VectorLabError, match="dimensions"):
        LocalVectorIndex(BadDimensionEmbedding()).build(chunk_sources(_sources()))

    failing = LocalVectorIndex(FailingEmbedding())
    with pytest.raises(VectorLabError, match="inference"):
        failing.build(chunk_sources(_sources()))
