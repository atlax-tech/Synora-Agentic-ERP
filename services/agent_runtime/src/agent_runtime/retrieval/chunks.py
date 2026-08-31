"""Deterministic, heading-aware chunks for the local retrieval baseline."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from agent_runtime.agent.contracts import canonical_json
from agent_runtime.retrieval.sources import CuratedSource

MAX_CHUNK_CHARS = 1_200
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")


@dataclass(frozen=True)
class SourceChunk:
    """One bounded chunk with all citation and scope metadata attached."""

    chunk_id: str
    ordinal: int
    section: str
    content: str
    content_digest: str
    title: str
    path: str
    source_type: str
    revision: str
    erp_version: str
    permission_scope: str
    ingested_at: str


def _section_blocks(source: CuratedSource) -> Iterable[tuple[str, str]]:
    section = source.title.strip()
    lines: list[str] = []
    for line in source.content.splitlines():
        heading = _HEADING.match(line)
        if heading:
            body = "\n".join(lines).strip()
            if body:
                yield section, body
            section = heading.group(1).strip() or source.title.strip()
            lines = []
        else:
            lines.append(line.rstrip())
    body = "\n".join(lines).strip()
    if body:
        yield section, body


def _split_block(text: str, limit: int) -> tuple[str, ...]:
    """Split by lines first, then hard-split an overlong line deterministically."""
    pieces: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        value = "\n".join(current).strip()
        if value:
            pieces.append(value)
        current = []
        current_length = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if len(line) > limit:
            flush()
            pieces.extend(line[offset : offset + limit] for offset in range(0, len(line), limit))
            continue
        candidate_length = len(line) if not current else current_length + 1 + len(line)
        if current and candidate_length > limit:
            flush()
        current.append(line)
        current_length = len(line) if len(current) == 1 else current_length + 1 + len(line)
    flush()
    return tuple(pieces)


def _chunk_id(source: CuratedSource, *, section: str, ordinal: int, content: str) -> str:
    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    identity = {
        "source_type": source.source_type,
        "path": source.path,
        "revision": source.revision,
        "erp_version": source.erp_version,
        "permission_scope": source.permission_scope,
        "section": section,
        "ordinal": ordinal,
        "content_digest": content_digest,
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def chunk_source(source: CuratedSource) -> tuple[SourceChunk, ...]:
    """Return stable, non-empty, heading-aware chunks for one curated source."""
    chunks: list[SourceChunk] = []
    ordinal = 1
    for section, body in _section_blocks(source):
        prefix = " ".join(section.split()) or source.title.strip()
        prefix = prefix[: MAX_CHUNK_CHARS - 1]
        body_limit = max(1, MAX_CHUNK_CHARS - len(prefix) - 2)
        for piece in _split_block(body, body_limit):
            content = f"{prefix}\n\n{piece}".strip()
            content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunks.append(
                SourceChunk(
                    chunk_id=_chunk_id(source, section=section, ordinal=ordinal, content=content),
                    ordinal=ordinal,
                    section=section,
                    content=content,
                    content_digest=content_digest,
                    title=source.title,
                    path=source.path,
                    source_type=source.source_type,
                    revision=source.revision,
                    erp_version=source.erp_version,
                    permission_scope=source.permission_scope,
                    ingested_at=source.ingested_at,
                )
            )
            ordinal += 1
    return tuple(chunks)


def _source_sort_key(source: CuratedSource) -> tuple[str, ...]:
    content_digest = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
    return (
        source.path,
        source.revision,
        source.source_type,
        source.erp_version,
        source.permission_scope,
        source.title,
        source.ingested_at,
        content_digest,
    )


def chunk_sources(sources: Iterable[CuratedSource]) -> tuple[SourceChunk, ...]:
    """Chunk sources in a stable identity order, independent of input ordering."""
    chunks: list[SourceChunk] = []
    seen: set[str] = set()
    for source in sorted(sources, key=_source_sort_key):
        for chunk in chunk_source(source):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            chunks.append(chunk)
    return tuple(chunks)
