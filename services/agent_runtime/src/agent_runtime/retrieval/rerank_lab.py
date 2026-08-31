"""Bounded local reranking experiment for Phase 8 T05."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from agent_runtime.retrieval.hybrid_lab import HybridHit
from agent_runtime.retrieval.vector_lab import VectorLabError, VectorLabUnavailable

MAX_RERANK_CANDIDATES = 10
MAX_RERANK_RESULTS = 20


@dataclass(frozen=True)
class RerankerModelSpec:
    model_id: str
    revision: str

    def __post_init__(self) -> None:
        if not self.model_id or not self.revision:
            raise ValueError("reranker model id and revision must be non-empty")


RERANKER_MODEL = RerankerModelSpec(
    model_id="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    revision="1427fd652930e4ba29e8149678df786c240d8825",
)


class Reranker(Protocol):
    model_id: str
    revision: str

    def score(self, query: str, text: str) -> float: ...


class CrossEncoderReranker:
    """Lazy adapter that asks a local cross-encoder for ranking scores only."""

    def __init__(self, model: Any, spec: RerankerModelSpec) -> None:
        self._model = model
        self.model_id = spec.model_id
        self.revision = spec.revision

    def score(self, query: str, text: str) -> float:
        try:
            values = self._model.predict([[query, text]], show_progress_bar=False)
            if len(values) != 1:
                raise VectorLabError("reranker returned an invalid score count")
            score = float(values[0])
        except VectorLabError:
            raise
        except Exception as exc:
            raise VectorLabError("local reranker inference failed") from exc
        if not math.isfinite(score):
            raise VectorLabError("reranker returned a non-finite score")
        return score


def load_local_reranker(spec: RerankerModelSpec = RERANKER_MODEL) -> Reranker:
    """Load the pinned cross-encoder on CPU; no remote ranking API is used."""
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise VectorLabUnavailable("sentence-transformers is not installed") from exc
    try:
        model = CrossEncoder(spec.model_id, revision=spec.revision, device="cpu")
    except Exception as exc:
        raise VectorLabUnavailable("pinned reranker model could not be loaded") from exc
    return CrossEncoderReranker(model, spec)


def rerank_hits(
    query: str,
    candidates: Sequence[HybridHit],
    reranker: Reranker,
    *,
    candidate_pool: int = MAX_RERANK_CANDIDATES,
    top_k: int = 5,
) -> tuple[HybridHit, ...]:
    """Rerank only a bounded hybrid pool, retaining the original chunk fields."""
    if not isinstance(query, str) or not query:
        return ()
    if (
        isinstance(candidate_pool, bool)
        or not isinstance(candidate_pool, int)
        or candidate_pool < 1
        or candidate_pool > MAX_RERANK_CANDIDATES
        or isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or top_k < 1
    ):
        return ()
    bounded = tuple(candidates[:candidate_pool])
    scored: list[HybridHit] = []
    for candidate in bounded:
        score = reranker.score(query, candidate.content)
        if not math.isfinite(score):
            raise VectorLabError("reranker returned a non-finite score")
        scored.append(replace(candidate, score=score))
    scored.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    return tuple(scored[: min(top_k, MAX_RERANK_RESULTS)])
