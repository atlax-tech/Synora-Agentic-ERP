"""Phase 8 T04 deterministic chunk, scoped search, and context adapter tests."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from agent_runtime.agent.context import (
    CONTEXT_INPUT_TOKEN_BUDGET_ENV,
    ContextBuilder,
    ContextBuildError,
    ContextFragment,
)
from agent_runtime.agent.prompting import NATIVE_AGENT_PROFILE_ID
from agent_runtime.retrieval.chunks import MAX_CHUNK_CHARS, chunk_source, chunk_sources
from agent_runtime.retrieval.index import RetrievalIndex
from agent_runtime.retrieval.sources import CuratedSource


def _source(
    *,
    path: str = "sop.md",
    revision: str = "v1",
    erp_version: str = "erp-a",
    source_type: str = "sop",
    permission_scope: str = "internal",
    content: str = "## Procedure\nneedle policy",
) -> CuratedSource:
    return CuratedSource(
        source_type=source_type,
        path=path,
        revision=revision,
        erp_version=erp_version,
        permission_scope=permission_scope,
        ingested_at="2026-08-31T00:00:00+00:00",
        title=path,
        content=content,
    )


def test_chunking_is_heading_aware_and_ignores_volatile_ingest_time() -> None:
    source = _source(
        content=("## First section\nalpha\nbeta\n\n## Second section\n" + ("long line " * 220))
    )
    first = chunk_source(source)
    second = chunk_source(replace(source, ingested_at="2026-09-01T00:00:00+00:00"))

    assert [
        (chunk.chunk_id, chunk.ordinal, chunk.section, chunk.content, chunk.content_digest)
        for chunk in first
    ] == [
        (chunk.chunk_id, chunk.ordinal, chunk.section, chunk.content, chunk.content_digest)
        for chunk in second
    ]
    assert first
    assert [chunk.ordinal for chunk in first] == list(range(1, len(first) + 1))
    assert all(chunk.content.strip() for chunk in first)
    assert all(len(chunk.content) <= MAX_CHUNK_CHARS for chunk in first)
    assert {chunk.section for chunk in first} == {"First section", "Second section"}
    assert all(chunk.chunk_id and len(chunk.content_digest) == 64 for chunk in first)

    changed_revision = chunk_source(replace(source, revision="v2"))
    changed_content = chunk_source(replace(source, content=source.content + "\nnew fact"))
    assert {chunk.chunk_id for chunk in first}.isdisjoint(
        chunk.chunk_id for chunk in changed_revision
    )
    assert {chunk.chunk_id for chunk in first} != {chunk.chunk_id for chunk in changed_content}


def test_chunk_sources_never_mix_source_identity_and_are_order_stable() -> None:
    left = _source(path="z.md", content="## Z\nz text")
    right = _source(path="a.md", content="## A\na text")

    chunks = chunk_sources((left, right))
    assert [chunk.path for chunk in chunks] == ["a.md", "z.md"]
    assert all(chunk.path in {"a.md", "z.md"} for chunk in chunks)
    assert all(len(chunk.content_digest) == 64 for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert len(chunk_sources((left, left, right))) == len(chunks)


def test_search_uses_chunk_rows_and_applies_metadata_before_limit(tmp_path: Path) -> None:
    old = _source(
        path="old.md",
        revision="v1",
        erp_version="erp-a",
        content="## Procedure\nneedle needle needle needle",
    )
    current = _source(
        path="current.md",
        revision="v2",
        erp_version="erp-b",
        content="## Procedure\nneedle current policy",
    )
    index = RetrievalIndex(str(tmp_path / "chunks.db"))
    try:
        assert index.ingest((old, current)) >= 2
        hits = index.search(
            "needle",
            limit=1,
            permission_scope="internal",
            source_type="sop",
            revision="v2",
            erp_version="erp-b",
        )
        assert len(hits) == 1
        hit = hits[0]
        assert hit.path == "current.md"
        assert hit.revision == "v2"
        assert hit.erp_version == "erp-b"
        assert hit.chunk_id
        assert hit.ordinal == 1
        assert len(hit.content_digest) == 64
        assert hit.content
        assert index.search("needle", permission_scope="internal", revision="missing") == []
    finally:
        index.close()


def test_rebuild_recovers_a_missing_fts_table(tmp_path: Path) -> None:
    db_path = tmp_path / "broken.db"
    index = RetrievalIndex(str(db_path))
    index.ingest((_source(content="## Procedure\nrebuild target"),))
    index.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE chunks_fts")
    with RetrievalIndex(str(db_path)) as rebuilt:
        assert rebuilt.rebuild((_source(content="## Procedure\nrebuild target"),)) == 1
        assert rebuilt.search("rebuild target")


def test_context_budget_fails_closed_when_retrieval_reference_does_not_fit() -> None:
    reference = ContextFragment.from_content(
        fragment_id="retrieval:optional",
        fragment_type="reference",
        source="retrieval:optional",
        version="v1",
        trust_level="UNTRUSTED",
        priority=400,
        content="optional retrieval " + ("fact " * 2_000),
    )
    with pytest.raises(ContextBuildError) as error:
        ContextBuilder().build(
            profile_id=NATIVE_AGENT_PROFILE_ID,
            goal="check stock",
            task_profile="REPLENISHMENT_ANALYSIS",
            tools=(),
            allowed_tools=frozenset(),
            reference_fragments=(reference,),
            environ={CONTEXT_INPUT_TOKEN_BUDGET_ENV: "3000"},
        )
    assert error.value.code == "CONTEXT_BUDGET"
