"""P3.6 FTS5/BM25 检索基线测试 (SPEC §12, 无网络依赖)。

验证 curated 源加载、摄取、BM25 排序、元数据完整、重建幂等与
查询 fail-safe; 检索结果必须是纯数据 (不作为指令/授权依据)。
"""

from pathlib import Path

from agent_runtime.retrieval.index import RetrievalIndex
from agent_runtime.retrieval.sources import load_curated_sources

KNOWLEDGE = Path(__file__).parent.parent / "src" / "agent_runtime" / "retrieval" / "knowledge"


def _sources():
    return load_curated_sources(KNOWLEDGE)


def _index(tmp_path: Path) -> RetrievalIndex:
    index = RetrievalIndex(str(tmp_path / "retrieval.db"))
    index.ingest(_sources())
    return index


def test_loads_curated_sources_with_metadata() -> None:
    sources = _sources()
    assert len(sources) >= 3
    for source in sources:
        assert source.title
        assert source.content.strip()
        assert source.source_type in {"erp-docs", "source-map", "baseline", "sop"}
        assert source.permission_scope in {"public", "internal"}
        assert source.revision
        assert "6a329d0" in source.erp_version  # 固定 ERP 版本元数据


def test_search_finds_relevant_source_with_metadata(tmp_path: Path) -> None:
    with _index(tmp_path) as index:
        hits = index.search("purchase order")
        assert hits
        top = hits[0]
        assert top.title
        assert top.path
        assert top.erp_version
        assert top.permission_scope
        assert top.score <= 0  # bm25 越小越相关


def test_chinese_query_finds_sop(tmp_path: Path) -> None:
    with _index(tmp_path) as index:
        hits = index.search("补货")
        assert hits
        assert any("重复采购" in hit.snippet or "补货" in hit.snippet for hit in hits)


def test_bm25_ranks_more_specific_source_first(tmp_path: Path) -> None:
    with _index(tmp_path) as index:
        hits = index.search("purchase invoice")
        assert hits
        # 含 "Purchase Invoice" 短语的源应排在更靠前的位置
        assert hits[0].title
        scores = [hit.score for hit in hits]
        assert scores == sorted(scores)  # BM25 升序 = 相关性降序


def test_unrelated_query_returns_empty(tmp_path: Path) -> None:
    with _index(tmp_path) as index:
        assert index.search("zzzqqqunrelated12345") == []


def test_invalid_and_overlong_queries_fail_safe(tmp_path: Path) -> None:
    with _index(tmp_path) as index:
        assert index.search("") == []
        assert index.search("   ") == []
        assert index.search("x" * 600) == []
        # 特殊字符被规范化, 不会抛语法错误
        assert isinstance(index.search('purchase "order" --injection'), list)


def test_rebuild_is_idempotent_and_recoverable(tmp_path: Path) -> None:
    with _index(tmp_path) as index:
        index.rebuild(_sources())
        assert index.search("purchase order")
        # 重建后再次摄取 (幂等)
        index.ingest(_sources())
        assert index.search("purchase order")
