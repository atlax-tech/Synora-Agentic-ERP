"""SQLite FTS5/BM25 chunk retrieval baseline.

The index is rebuildable infrastructure, not an ERP or Memory authority. Every
hit keeps its source, revision, scope, and content digest so later callers can
validate citations without treating retrieved text as instructions.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from agent_runtime.retrieval.chunks import chunk_sources
from agent_runtime.retrieval.sources import PERMISSION_SCOPES, CuratedSource

_MAX_QUERY_LENGTH = 500
_FTS5_PUNCTUATION = re.compile(r"[^\w\u4e00-\u9fff]+")


@dataclass(frozen=True)
class SearchHit:
    title: str
    path: str
    source_type: str
    revision: str
    erp_version: str
    permission_scope: str
    ingested_at: str
    score: float
    snippet: str
    chunk_id: str = ""
    ordinal: int = 0
    section: str = ""
    content_digest: str = ""
    content: str = ""


class RetrievalIndex:
    def __init__(self, db_path: str) -> None:
        self._connection = sqlite3.connect(db_path)
        self._connection.row_factory = sqlite3.Row
        self._create_schema()
        self._connection.commit()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks(
              id INTEGER PRIMARY KEY,
              chunk_id TEXT NOT NULL UNIQUE,
              ordinal INTEGER NOT NULL,
              title TEXT NOT NULL,
              section TEXT NOT NULL,
              path TEXT NOT NULL,
              source_type TEXT NOT NULL,
              revision TEXT NOT NULL,
              erp_version TEXT NOT NULL,
              permission_scope TEXT NOT NULL,
              ingested_at TEXT NOT NULL,
              content_digest TEXT NOT NULL,
              content TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              content,
              content='chunks',
              content_rowid='id',
              tokenize='unicode61'
            )
            """
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> RetrievalIndex:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _sync_fts(self) -> None:
        self._connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")

    def ingest(self, sources: tuple[CuratedSource, ...]) -> int:
        """Replace the local index with deterministic chunks and return chunk count."""
        chunks = chunk_sources(sources)
        self._create_schema()
        self._connection.execute("DELETE FROM chunks")
        self._connection.executemany(
            """
            INSERT INTO chunks(
              chunk_id, ordinal, title, section, path, source_type, revision,
              erp_version, permission_scope, ingested_at, content_digest, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    chunk.chunk_id,
                    chunk.ordinal,
                    chunk.title,
                    chunk.section,
                    chunk.path,
                    chunk.source_type,
                    chunk.revision,
                    chunk.erp_version,
                    chunk.permission_scope,
                    chunk.ingested_at,
                    chunk.content_digest,
                    chunk.content,
                )
                for chunk in chunks
            ),
        )
        self._sync_fts()
        self._connection.commit()
        return len(chunks)

    def rebuild(self, sources: tuple[CuratedSource, ...]) -> int:
        """Drop and recreate only the rebuildable chunk index."""
        self._connection.execute("DROP TABLE IF EXISTS chunks_fts")
        self._connection.execute("DROP TABLE IF EXISTS chunks")
        self._create_schema()
        return self.ingest(sources)

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Convert user text to a safe FTS5 phrase."""
        return _FTS5_PUNCTUATION.sub(" ", query).strip()

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    @staticmethod
    def _metadata_filter(
        permission_scope: str,
        *,
        source_type: str | None,
        revision: str | None,
        erp_version: str | None,
    ) -> tuple[list[str], list[str]] | None:
        if permission_scope not in PERMISSION_SCOPES:
            return None
        clauses = ["c.permission_scope = ?"]
        values = [permission_scope]
        for column, value in (
            ("source_type", source_type),
            ("revision", revision),
            ("erp_version", erp_version),
        ):
            if value is not None:
                if not isinstance(value, str) or not value:
                    return None
                clauses.append(f"c.{column} = ?")
                values.append(value)
        return clauses, values

    @staticmethod
    def _hit_from_row(row: sqlite3.Row, *, score: float, snippet: str) -> SearchHit:
        return SearchHit(
            title=row["title"],
            path=row["path"],
            source_type=row["source_type"],
            revision=row["revision"],
            erp_version=row["erp_version"],
            permission_scope=row["permission_scope"],
            ingested_at=row["ingested_at"],
            score=score,
            snippet=snippet,
            chunk_id=row["chunk_id"],
            ordinal=int(row["ordinal"]),
            section=row["section"],
            content_digest=row["content_digest"],
            content=row["content"],
        )

    def search(
        self,
        query: str,
        limit: int = 5,
        permission_scope: str = "internal",
        *,
        source_type: str | None = None,
        revision: str | None = None,
        erp_version: str | None = None,
    ) -> list[SearchHit]:
        """Search chunk rows with mandatory scope and optional metadata filters."""
        if not isinstance(query, str) or len(query) > _MAX_QUERY_LENGTH:
            return []
        normalized = self._normalize_query(query)
        if not normalized:
            return []
        filters = self._metadata_filter(
            permission_scope,
            source_type=source_type,
            revision=revision,
            erp_version=erp_version,
        )
        if filters is None:
            return []
        clauses, values = filters
        if isinstance(limit, bool) or not isinstance(limit, int):
            return []
        safe_limit = max(1, min(limit, 20))
        where = ["chunks_fts MATCH ?", *clauses]
        try:
            rows = self._connection.execute(
                f"""
                SELECT c.*, bm25(chunks_fts) AS score,
                       snippet(chunks_fts, 0, '<b>', '</b>', '…', 64) AS snippet
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE {" AND ".join(where)}
                ORDER BY bm25(chunks_fts), c.chunk_id
                LIMIT ?
                """,
                (f'"{normalized}"', *values, safe_limit * 4),
            ).fetchall()
        except sqlite3.DatabaseError:
            rows = []
        hits = [
            self._hit_from_row(row, score=float(row["score"]), snippet=row["snippet"] or "")
            for row in rows
        ]
        if self._contains_cjk(query):
            hits = self._append_substring_hits(
                hits,
                query,
                safe_limit,
                permission_scope,
                source_type=source_type,
                revision=revision,
                erp_version=erp_version,
            )
        return hits[:safe_limit]

    def _append_substring_hits(
        self,
        hits: list[SearchHit],
        query: str,
        limit: int,
        permission_scope: str = "internal",
        *,
        source_type: str | None = None,
        revision: str | None = None,
        erp_version: str | None = None,
    ) -> list[SearchHit]:
        """CJK literal fallback for unicode61 tokenization gaps."""
        filters = self._metadata_filter(
            permission_scope,
            source_type=source_type,
            revision=revision,
            erp_version=erp_version,
        )
        if filters is None:
            return hits
        clauses, values = filters
        existing = {hit.chunk_id for hit in hits}
        try:
            rows = self._connection.execute(
                f"""
                SELECT c.*
                FROM chunks c
                WHERE instr(c.content, ?) > 0 AND {" AND ".join(clauses)}
                ORDER BY c.path, c.ordinal, c.chunk_id
                LIMIT ?
                """,
                (query, *values, limit * 4),
            ).fetchall()
        except sqlite3.DatabaseError:
            return hits
        for row in rows:
            if len(hits) >= limit:
                break
            if row["chunk_id"] in existing:
                continue
            hits.append(
                self._hit_from_row(
                    row,
                    score=0.0,
                    snippet=self._substring_snippet(row["content"], query),
                )
            )
            existing.add(row["chunk_id"])
        return hits

    @staticmethod
    def _substring_snippet(content: str, query: str, radius: int = 40) -> str:
        index = content.find(query)
        if index < 0:
            return content[: radius * 2]
        start = max(0, index - radius)
        end = min(len(content), index + len(query) + radius)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(content) else ""
        return f"{prefix}{content[start:end]}{suffix}"
