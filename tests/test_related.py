"""轻量关联发现测试"""

import tempfile
from pathlib import Path

from shibei.config import Config
from shibei.related import RelatedFinder
from shibei.bm25 import BM25Index


def make_config(db_path: str) -> Config:
    cfg = Config()
    cfg.storage.path = db_path
    return cfg


def test_related_by_tags():
    """基于标签共现发现相关文档。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = make_config(tmpdir)
        bm25 = BM25Index(str(Path(tmpdir) / "fts5.db"))
        bm25.add_batch([
            ("a.md", "a.md", "ai,llm", "大模型技术细节"),
            ("b.md", "b.md", "ai,gpu", "GPU 并行计算"),
            ("c.md", "c.md", "finance", "金融风险管理"),
        ])

        finder = RelatedFinder(cfg)
        related = finder.find_related("a.md", limit=5)
        sources = [r["source"] for r in related]
        assert "b.md" in sources
        assert "c.md" not in sources
        assert any("共享标签" in r["reason"] for r in related)


def test_related_by_markdown_link():
    """基于 Markdown 内链发现相关文档。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = make_config(tmpdir)
        bm25 = BM25Index(str(Path(tmpdir) / "fts5.db"))
        bm25.add_batch([
            ("a.md", str(Path(tmpdir) / "a.md"), "", "参见 [[b.md]] 的内容"),
            ("b.md", str(Path(tmpdir) / "b.md"), "", "B 文档内容"),
        ])

        finder = RelatedFinder(cfg)
        related = finder.find_related("a.md", limit=5)
        sources = [r["source"] for r in related]
        assert "b.md" in sources
        assert any("链接" in r["reason"] for r in related)


def test_related_no_crash_on_empty():
    """空库时不应报错。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = make_config(tmpdir)
        BM25Index(str(Path(tmpdir) / "fts5.db"))
        finder = RelatedFinder(cfg)
        assert finder.find_related("a.md") == []
