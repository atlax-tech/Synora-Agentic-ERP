"""Local vector retrieval experiment for Phase 8 T05.

This module is deliberately outside the Runtime business path.  It consumes
the deterministic ``SourceChunk`` objects produced by T04 and returns hits
whose citation metadata is copied from those chunks.  Permissions and source
version filters are applied before ranking results are returned.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agent_runtime.retrieval.chunks import SourceChunk
from agent_runtime.retrieval.sources import PERMISSION_SCOPES

_MAX_QUERY_LENGTH = 500
_MAX_RESULTS = 20
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
VECTOR_MIN_COSINE = 0.80


class VectorLabError(RuntimeError):
    """A local vector experiment cannot produce trustworthy results."""


class VectorLabUnavailable(VectorLabError):
    """The optional local model stack is not available."""


@dataclass(frozen=True)
class EmbeddingModelSpec:
    model_id: str
    revision: str

    def __post_init__(self) -> None:
        if not self.model_id or not self.revision:
            raise ValueError("embedding model id and revision must be non-empty")


EMBEDDING_MODEL = EmbeddingModelSpec(
    model_id="intfloat/multilingual-e5-small",
    revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
)


class EmbeddingEncoder(Protocol):
    model_id: str
    revision: str

    def encode(
        self,
        texts: Sequence[str],
        *,
        prefix: Literal["query", "passage"],
    ) -> Sequence[Sequence[float]]: ...


class SentenceTransformerEmbedding:
    """Small lazy adapter around the optional sentence-transformers package."""

    def __init__(self, model: Any, spec: EmbeddingModelSpec) -> None:
        self._model = model
        self.model_id = spec.model_id
        self.revision = spec.revision

    def encode(
        self,
        texts: Sequence[str],
        *,
        prefix: Literal["query", "passage"],
    ) -> Sequence[Sequence[float]]:
        marker = _QUERY_PREFIX if prefix == "query" else _PASSAGE_PREFIX
        try:
            raw_vectors = self._model.encode(
                [f"{marker}{text}" for text in texts],
                convert_to_numpy=False,
                normalize_embeddings=False,
                show_progress_bar=False,
            )
            return tuple(tuple(float(value) for value in row) for row in raw_vectors)
        except Exception as exc:
            raise VectorLabError("local embedding inference failed") from exc


def load_local_embedding_model(
    spec: EmbeddingModelSpec = EMBEDDING_MODEL,
) -> EmbeddingEncoder:
    """Load the pinned model on CPU; never call a remote inference endpoint."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise VectorLabUnavailable("sentence-transformers is not installed") from exc
    try:
        model = SentenceTransformer(spec.model_id, revision=spec.revision, device="cpu")
    except Exception as exc:
        raise VectorLabUnavailable("pinned embedding model could not be loaded") from exc
    return SentenceTransformerEmbedding(model, spec)


@dataclass(frozen=True)
class VectorHit:
    """A vector-ranked hit with the same source metadata as a SearchHit."""

    title: str
    path: str
    source_type: str
    revision: str
    erp_version: str
    permission_scope: str
    ingested_at: str
    score: float
    snippet: str
    chunk_id: str
    ordinal: int
    section: str
    content_digest: str
    content: str

    @classmethod
    def from_chunk(cls, chunk: SourceChunk, *, score: float) -> VectorHit:
        return cls(
            title=chunk.title,
            path=chunk.path,
            source_type=chunk.source_type,
            revision=chunk.revision,
            erp_version=chunk.erp_version,
            permission_scope=chunk.permission_scope,
            ingested_at=chunk.ingested_at,
            score=score,
            snippet=chunk.content[:200],
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            section=chunk.section,
            content_digest=chunk.content_digest,
            content=chunk.content,
        )


def _normalise(vector: Sequence[float]) -> tuple[float, ...]:
    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VectorLabError("embedding vector is not numeric") from exc
    if not values or any(not math.isfinite(value) for value in values):
        raise VectorLabError("embedding vector is empty or non-finite")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0:
        raise VectorLabError("embedding vector has no usable norm")
    return tuple(value / norm for value in values)


def _safe_limit(limit: int) -> int | None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        return None
    return max(1, min(limit, _MAX_RESULTS))


def _matches(
    chunk: SourceChunk,
    permission_scope: str,
    *,
    source_type: str | None,
    revision: str | None,
    erp_version: str | None,
) -> bool:
    if permission_scope not in PERMISSION_SCOPES or chunk.permission_scope != permission_scope:
        return False
    for actual, expected in (
        (chunk.source_type, source_type),
        (chunk.revision, revision),
        (chunk.erp_version, erp_version),
    ):
        if expected is not None and (not isinstance(expected, str) or not expected):
            return False
        if expected is not None and actual != expected:
            return False
    return True


def _valid_chunk(chunk: SourceChunk) -> bool:
    return (
        bool(chunk.content)
        and len(chunk.content) <= 16_000
        and bool(_DIGEST.fullmatch(chunk.chunk_id))
        and bool(_DIGEST.fullmatch(chunk.content_digest))
        and hashlib.sha256(chunk.content.encode("utf-8")).hexdigest() == chunk.content_digest
    )


class LocalVectorIndex:
    """In-memory cosine index for the fixed Phase 8 comparison corpus."""

    def __init__(
        self, encoder: EmbeddingEncoder, *, min_similarity: float = VECTOR_MIN_COSINE
    ) -> None:
        if not math.isfinite(min_similarity) or not -1 <= min_similarity <= 1:
            raise ValueError("vector similarity threshold must be finite and within cosine range")
        self._encoder = encoder
        self._min_similarity = min_similarity
        self._chunks: tuple[SourceChunk, ...] = ()
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._dimension: int | None = None
        self._built = False
        self.build_latency_ms = 0.0

    @property
    def model_id(self) -> str:
        return self._encoder.model_id

    @property
    def model_revision(self) -> str:
        return self._encoder.revision

    @property
    def dimension(self) -> int | None:
        return self._dimension

    @property
    def index_size_bytes(self) -> int:
        if self._dimension is None:
            return 0
        return len(self._chunks) * self._dimension * 8

    def build(self, chunks: Sequence[SourceChunk]) -> int:
        started = time.perf_counter()
        ordered = tuple(sorted(chunks, key=lambda chunk: chunk.chunk_id))
        if len({chunk.chunk_id for chunk in ordered}) != len(ordered):
            raise VectorLabError("duplicate chunk identity")
        if any(not _valid_chunk(chunk) for chunk in ordered):
            raise VectorLabError("chunk citation or digest is invalid")
        if ordered:
            try:
                raw_vectors = self._encoder.encode(
                    tuple(chunk.content for chunk in ordered), prefix="passage"
                )
            except VectorLabError:
                raise
            except Exception as exc:
                raise VectorLabError("local embedding inference failed") from exc
            if len(raw_vectors) != len(ordered):
                raise VectorLabError("embedding count does not match chunk count")
            vectors = tuple(_normalise(vector) for vector in raw_vectors)
            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) != 1:
                raise VectorLabError("embedding dimensions do not match")
            self._dimension = len(vectors[0])
            self._vectors = {
                chunk.chunk_id: vector for chunk, vector in zip(ordered, vectors, strict=True)
            }
        else:
            self._dimension = None
            self._vectors = {}
        self._chunks = ordered
        self._built = True
        self.build_latency_ms = (time.perf_counter() - started) * 1000
        return len(ordered)

    def search(
        self,
        query: str,
        limit: int = 5,
        permission_scope: str = "internal",
        *,
        source_type: str | None = None,
        revision: str | None = None,
        erp_version: str | None = None,
    ) -> list[VectorHit]:
        if not isinstance(query, str) or not query or len(query) > _MAX_QUERY_LENGTH:
            return []
        safe_limit = _safe_limit(limit)
        if safe_limit is None or not self._built:
            return []
        if permission_scope not in PERMISSION_SCOPES:
            return []
        try:
            raw_query = self._encoder.encode((query,), prefix="query")
            if len(raw_query) != 1:
                raise VectorLabError("query embedding count does not match")
            query_vector = _normalise(raw_query[0])
        except VectorLabError:
            raise
        except Exception as exc:
            raise VectorLabError("local query embedding failed") from exc
        if self._dimension != len(query_vector):
            raise VectorLabError("query embedding dimension does not match index")

        ranked: list[VectorHit] = []
        for chunk in self._chunks:
            if not _matches(
                chunk,
                permission_scope,
                source_type=source_type,
                revision=revision,
                erp_version=erp_version,
            ):
                continue
            vector = self._vectors[chunk.chunk_id]
            score = sum(left * right for left, right in zip(query_vector, vector, strict=True))
            if math.isfinite(score) and score >= self._min_similarity:
                ranked.append(VectorHit.from_chunk(chunk, score=score))
        ranked.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return ranked[:safe_limit]
