"""SQLite FTS5/BM25 检索索引 (SPEC §12.2 "SQLite FTS5/BM25 baseline")。

- 索引可重建 (rebuild 幂等), 内容表 + 外部内容 FTS5 虚拟表;
- 查询按 BM25 排序 (值越小越相关), 返回片段与完整元数据;
- 检索结果只是数据: 调用方不得将其作为系统指令、授权或工具选择依据;
- 查询异常 (非法语法/空) 返回空结果, 不抛错, 检索失败不影响业务。
"""

import re
import sqlite3
from dataclasses import dataclass

from agent_runtime.retrieval.sources import CuratedSource

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


class RetrievalIndex:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._connection = sqlite3.connect(db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sources(
              id INTEGER PRIMARY KEY,
              title TEXT NOT NULL,
              path TEXT NOT NULL,
              source_type TEXT NOT NULL,
              revision TEXT NOT NULL,
              erp_version TEXT NOT NULL,
              permission_scope TEXT NOT NULL,
              ingested_at TEXT NOT NULL,
              content TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(
              content,
              content='sources',
              content_rowid='id',
              tokenize='unicode61'
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> RetrievalIndex:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _sync_fts(self) -> None:
        self._connection.execute("INSERT INTO sources_fts(sources_fts) VALUES('rebuild')")

    def ingest(self, sources: tuple[CuratedSource, ...]) -> int:
        """幂等摄取: 先清空再插入 (rebuildable index)。"""
        self._connection.execute("DELETE FROM sources")
        for source in sources:
            self._connection.execute(
                """
                INSERT INTO sources(
                  title, path, source_type, revision, erp_version,
                  permission_scope, ingested_at, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.title,
                    source.path,
                    source.source_type,
                    source.revision,
                    source.erp_version,
                    source.permission_scope,
                    source.ingested_at,
                    source.content,
                ),
            )
        self._sync_fts()
        self._connection.commit()
        return len(sources)

    def rebuild(self, sources: tuple[CuratedSource, ...]) -> int:
        """重建索引 (删除并重建 FTS 后重摄取)。"""
        self._connection.execute("DROP TABLE IF EXISTS sources_fts")
        self._connection.execute("DELETE FROM sources")
        self._connection.execute(
            """
            CREATE VIRTUAL TABLE sources_fts USING fts5(
              content,
              content='sources',
              content_rowid='id',
              tokenize='unicode61'
            )
            """
        )
        return self.ingest(sources)

    @staticmethod
    def _normalize_query(query: str) -> str:
        """把用户查询转成 FTS5 安全短语: 非词字符转为空格, 整体作短语匹配。"""
        normalized = _FTS5_PUNCTUATION.sub(" ", query).strip()
        return normalized

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        """按 BM25 排序检索; 非法/空查询返回空列表。

        unicode61 分词器把连续中文当作一个 token ("建议补货" != "补货"),
        因此对含 CJK 的查询补充子串召回通道, 避免整词 token 漏检; FTS5
        命中 (BM25 序) 在前, 子串补充在后。
        """
        normalized = self._normalize_query(query)
        if not normalized or len(query) > _MAX_QUERY_LENGTH:
            return []
        safe_limit = max(1, min(limit, 20))
        try:
            rows = self._connection.execute(
                """
                SELECT s.id, s.title, s.path, s.source_type, s.revision, s.erp_version,
                       s.permission_scope, s.ingested_at,
                       bm25(sources_fts) AS score,
                       snippet(sources_fts, 0, '<b>', '</b>', '…', 12) AS snippet
                FROM sources_fts
                JOIN sources s ON s.id = sources_fts.rowid
                WHERE sources_fts MATCH ?
                ORDER BY bm25(sources_fts)
                LIMIT ?
                """,
                (f'"{normalized}"', safe_limit * 4),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        hits = [
            SearchHit(
                title=row["title"],
                path=row["path"],
                source_type=row["source_type"],
                revision=row["revision"],
                erp_version=row["erp_version"],
                permission_scope=row["permission_scope"],
                ingested_at=row["ingested_at"],
                score=float(row["score"]),
                snippet=row["snippet"] or "",
            )
            for row in rows
        ]
        if self._contains_cjk(query):
            hits = self._append_substring_hits(hits, query, safe_limit)
        return hits[:safe_limit]

    def _append_substring_hits(
        self, hits: list[SearchHit], query: str, limit: int
    ) -> list[SearchHit]:
        """中文子串补充: FTS 未召回但 content 包含查询子串的源, 按路径序附加。"""
        fts_paths = {hit.path for hit in hits}
        try:
            rows = self._connection.execute(
                "SELECT * FROM sources WHERE content LIKE ? ORDER BY path",
                (f"%{query}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            return hits
        for row in rows:
            if len(hits) >= limit:
                break
            if row["path"] in fts_paths:
                continue
            hits.append(
                SearchHit(
                    title=row["title"],
                    path=row["path"],
                    source_type=row["source_type"],
                    revision=row["revision"],
                    erp_version=row["erp_version"],
                    permission_scope=row["permission_scope"],
                    ingested_at=row["ingested_at"],
                    score=0.0,
                    snippet=self._substring_snippet(row["content"], query),
                )
            )
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
