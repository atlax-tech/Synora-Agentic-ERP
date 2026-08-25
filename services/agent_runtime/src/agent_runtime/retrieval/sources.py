"""Curated 检索源模型 (SPEC §12.1 Source requirements)。

每个 source 带固定 ERP 版本、source type、路径/URL、revision、permission
scope 与摄取时间; 内容由人工精选 (curated), 指向权威文档证据。
检索内容一律视为数据, 永不作为系统指令、授权或工具选择依据。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# 固定 ERP 版本对 (ADR-0002)。
ERP_VERSION = "frappe 6a329d0 / erpnext 11e0ba0"

# 内置 curated 知识目录 (相对本文件)。
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

SOURCE_TYPES = ("erp-docs", "source-map", "baseline", "sop")
PERMISSION_SCOPES = ("public", "internal")


@dataclass(frozen=True)
class CuratedSource:
    source_type: str
    path: str
    revision: str
    erp_version: str
    permission_scope: str
    ingested_at: str
    title: str
    content: str

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"unknown source_type: {self.source_type}")
        if self.permission_scope not in PERMISSION_SCOPES:
            raise ValueError(f"unknown permission_scope: {self.permission_scope}")
        if not self.path or not self.title or not self.content.strip():
            raise ValueError("source path/title/content must be non-empty")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_markdown_source(path: Path, ingested_at: str) -> CuratedSource:
    """从 curated Markdown 前注解析元数据: 首行标题 + `key: value` 块。

    格式 (knowledge/*.md):
        # Title
        source_type: erp-docs
        revision: v1
        permission_scope: internal
        ---
        <正文>
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    meta: dict[str, str] = {}
    body: list[str] = []
    in_body = False
    for line in lines:
        if not in_body:
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("---"):
                in_body = True
            elif ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
            continue
        body.append(line)
    if not title or not body:
        raise ValueError(f"invalid curated source format: {path}")
    return CuratedSource(
        source_type=meta.get("source_type", "erp-docs"),
        path=str(path),
        revision=meta.get("revision", "v1"),
        erp_version=ERP_VERSION,
        permission_scope=meta.get("permission_scope", "internal"),
        ingested_at=ingested_at,
        title=title,
        content="\n".join(body).strip(),
    )


def load_curated_sources(directory: Path = KNOWLEDGE_DIR) -> tuple[CuratedSource, ...]:
    """加载 knowledge/ 目录下全部 curated Markdown (确定性顺序)。"""
    ingested_at = _now()
    return tuple(
        _parse_markdown_source(path, ingested_at) for path in sorted(directory.glob("*.md"))
    )
