"""Deterministic Reciprocal Rank Fusion for the Phase 8 retrieval lab."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from agent_runtime.retrieval.index import SearchHit
from agent_runtime.retrieval.sources import PERMISSION_SCOPES
from agent_runtime.retrieval.vector_lab import VectorHit

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_RESULTS = 20


type FusionInputHit = SearchHit | VectorHit


@dataclass(frozen=True)
class HybridHit:
    """A fused hit whose source metadata remains owned by an original chunk."""

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
    fts_rank: int | None
    vector_rank: int | None

    @classmethod
    def from_hit(
        cls,
        hit: FusionInputHit,
        *,
        score: float,
        fts_rank: int | None,
        vector_rank: int | None,
    ) -> HybridHit:
        return cls(
            title=hit.title,
            path=hit.path,
            source_type=hit.source_type,
            revision=hit.revision,
            erp_version=hit.erp_version,
            permission_scope=hit.permission_scope,
            ingested_at=hit.ingested_at,
            score=score,
            snippet=hit.snippet,
            chunk_id=hit.chunk_id,
            ordinal=hit.ordinal,
            section=hit.section,
            content_digest=hit.content_digest,
            content=hit.content,
            fts_rank=fts_rank,
            vector_rank=vector_rank,
        )


@dataclass
class _FusionEntry:
    hit: FusionInputHit
    signature: tuple[object, ...]
    score: float = 0.0
    fts_rank: int | None = None
    vector_rank: int | None = None
    conflicting: bool = False


def _safe_limit(limit: int) -> int | None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        return None
    return max(1, min(limit, _MAX_RESULTS))


def _matches(
    hit: FusionInputHit,
    permission_scope: str,
    *,
    source_type: str | None,
    revision: str | None,
    erp_version: str | None,
) -> bool:
    if permission_scope not in PERMISSION_SCOPES or hit.permission_scope != permission_scope:
        return False
    for actual, expected in (
        (hit.source_type, source_type),
        (hit.revision, revision),
        (hit.erp_version, erp_version),
    ):
        if expected is not None and (not isinstance(expected, str) or not expected):
            return False
        if expected is not None and actual != expected:
            return False
    return True


def _valid_hit(
    hit: FusionInputHit,
    permission_scope: str,
    *,
    source_type: str | None,
    revision: str | None,
    erp_version: str | None,
) -> bool:
    return (
        _matches(
            hit,
            permission_scope,
            source_type=source_type,
            revision=revision,
            erp_version=erp_version,
        )
        and bool(_DIGEST.fullmatch(hit.chunk_id))
        and bool(_DIGEST.fullmatch(hit.content_digest))
        and bool(hit.content)
        and len(hit.content) <= 16_000
        and hashlib.sha256(hit.content.encode("utf-8")).hexdigest() == hit.content_digest
    )


def _signature(hit: FusionInputHit) -> tuple[object, ...]:
    return (
        hit.title,
        hit.path,
        hit.source_type,
        hit.revision,
        hit.erp_version,
        hit.permission_scope,
        hit.ingested_at,
        hit.ordinal,
        hit.section,
        hit.content_digest,
        hit.content,
    )


def reciprocal_rank_fusion(
    fts_hits: Sequence[SearchHit],
    vector_hits: Sequence[VectorHit],
    *,
    top_k: int = 5,
    rrf_k: int = 60,
    permission_scope: str = "internal",
    source_type: str | None = None,
    revision: str | None = None,
    erp_version: str | None = None,
) -> tuple[HybridHit, ...]:
    """Fuse ranked lists without adding incomparable BM25/cosine scores."""
    safe_limit = _safe_limit(top_k)
    if safe_limit is None or isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k <= 0:
        return ()

    entries: dict[str, _FusionEntry] = {}

    def add_hits(hits: Sequence[FusionInputHit], channel: str) -> None:
        for rank, hit in enumerate(hits, start=1):
            if not _valid_hit(
                hit,
                permission_scope,
                source_type=source_type,
                revision=revision,
                erp_version=erp_version,
            ):
                continue
            entry = entries.get(hit.chunk_id)
            if entry is None:
                entry = _FusionEntry(hit=hit, signature=_signature(hit))
                entries[hit.chunk_id] = entry
            elif entry.signature != _signature(hit):
                entry.conflicting = True
                continue
            if channel == "fts":
                if entry.fts_rank is not None:
                    continue
                entry.fts_rank = rank
            else:
                if entry.vector_rank is not None:
                    continue
                entry.vector_rank = rank
            entry.score += 1.0 / (rrf_k + rank)

    add_hits(fts_hits, "fts")
    add_hits(vector_hits, "vector")
    fused = [
        HybridHit.from_hit(
            entry.hit,
            score=entry.score,
            fts_rank=entry.fts_rank,
            vector_rank=entry.vector_rank,
        )
        for entry in entries.values()
        if not entry.conflicting and math.isfinite(entry.score)
    ]
    fused.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    return tuple(fused[:safe_limit])


def fuse_ranked_hits(
    fts_hits: Sequence[SearchHit],
    vector_hits: Sequence[VectorHit],
    **kwargs: object,
) -> tuple[HybridHit, ...]:
    """Backward-friendly alias for the fixed RRF experiment."""
    return reciprocal_rank_fusion(fts_hits, vector_hits, **kwargs)  # type: ignore[arg-type]
