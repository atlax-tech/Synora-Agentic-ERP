"""Convert retrieval hits into explicitly untrusted ContextBuilder references."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from agent_runtime.agent.context import ContextFragment
from agent_runtime.agent.contracts import canonical_json
from agent_runtime.retrieval.index import SearchHit
from agent_runtime.retrieval.sources import PERMISSION_SCOPES

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def search_hits_to_context_fragments(
    hits: Sequence[SearchHit],
) -> tuple[ContextFragment, ...]:
    """Build bounded reference fragments without granting retrieved text authority."""
    fragments: list[ContextFragment] = []
    seen: set[str] = set()
    for hit in hits:
        if (
            not _DIGEST.fullmatch(hit.chunk_id)
            or not _DIGEST.fullmatch(hit.content_digest)
            or hit.ordinal < 1
            or not hit.content
            or not hit.revision
            or hit.permission_scope not in PERMISSION_SCOPES
            or len(hit.content) > 16_000
            or hashlib.sha256(hit.content.encode("utf-8")).hexdigest() != hit.content_digest
        ):
            continue
        if hit.chunk_id in seen:
            continue
        payload = canonical_json(
            {
                "retrieval_provenance": {
                    "chunk_id": hit.chunk_id,
                    "ordinal": hit.ordinal,
                    "title": hit.title,
                    "path": hit.path,
                    "section": hit.section,
                    "source_type": hit.source_type,
                    "revision": hit.revision,
                    "erp_version": hit.erp_version,
                    "permission_scope": hit.permission_scope,
                    "content_digest": hit.content_digest,
                },
                "retrieved_text": hit.content,
            }
        )
        try:
            fragment = ContextFragment.from_content(
                fragment_id=f"retrieval:{hit.chunk_id}",
                fragment_type="reference",
                source=f"retrieval:{hit.chunk_id}",
                version=hit.revision,
                trust_level="UNTRUSTED",
                priority=400,
                content=payload,
            )
        except ValueError:
            continue
        seen.add(hit.chunk_id)
        fragments.append(fragment)
    return tuple(fragments)


def context_fragments_from_hits(hits: Sequence[SearchHit]) -> tuple[ContextFragment, ...]:
    """Backward-friendly name for the retrieval-to-context adapter."""
    return search_hits_to_context_fragments(hits)
