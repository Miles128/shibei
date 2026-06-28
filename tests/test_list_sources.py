"""list_sources 分页 + 摘要测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, ChromaConfig
from shibei.bm25 import BM25Index
from shibei.importer import list_sources


def _cfg(db_path: Path) -> Config:
    return Config(
        chroma=ChromaConfig(path=str(db_path), collection="test_kb", search_engine="bm25"),
        sources=[],
    )


def test_list_sources_empty():
    """空库返回 summary"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        cfg = _cfg(db_path)
        result = list_sources(cfg)
        assert result["count"] == 0
        assert result["sources"] == []
        assert result["summary"]["total_chunks"] == 0
        assert result["summary"]["total_files"] == 0


def test_list_sources_with_data():
    """有数据时返回 source 列表和摘要"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        cfg = _cfg(db_path)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/a.md", "/a.md", "ai,finance", "AI finance content", "", ""),
            ("/a.md", "/a.md", "ai,finance", "AI finance content 2", "", ""),
            ("/b.md", "/b.md", "ai", "AI only", "", ""),
        ])

        result = list_sources(cfg)
        assert result["count"] == 2
        assert "/a.md" in result["sources"]
        assert "/b.md" in result["sources"]
        assert result["summary"]["total_chunks"] == 3
        assert result["summary"]["total_files"] == 2
        # 标签统计
        by_tag = result["summary"]["by_tag"]
        assert "ai" in by_tag
        assert "finance" in by_tag


def test_list_sources_pagination():
    """分页功能"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        cfg = _cfg(db_path)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        # 插入 5 个不同 source
        for i in range(5):
            bm25.add_batch([
                (f"/file{i}.md", f"/file{i}.md", "ai", f"content {i}", "", ""),
            ])

        # 第一页 2 条
        page1 = list_sources(cfg, limit=2, offset=0)
        assert page1["count"] == 5
        assert len(page1["sources"]) == 2
        assert page1["has_more"] is True

        # 第二页 2 条
        page2 = list_sources(cfg, limit=2, offset=2)
        assert len(page2["sources"]) == 2
        assert page2["has_more"] is True

        # 第三页 1 条
        page3 = list_sources(cfg, limit=2, offset=4)
        assert len(page3["sources"]) == 1
        assert page3["has_more"] is False

        # 三页合并不重复
        all_sources = page1["sources"] + page2["sources"] + page3["sources"]
        assert len(set(all_sources)) == 5
