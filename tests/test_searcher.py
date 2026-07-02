"""Searcher 测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig
from shibei.searcher import Searcher


def _make_bm25_db(tmpdir: str):
    from shibei.bm25 import BM25Index
    db_path = Path(tmpdir)
    bm25 = BM25Index(str(db_path / "fts5.db"))
    bm25.add_batch([
        ("/ai.md", "/ai.md", "ai", "人工智能正在改变世界。", "", ""),
        ("/llm.md", "/llm.md", "ai", "大模型技术突破。", "", ""),
    ])
    return db_path


def test_searcher_bm25_basic():
    """BM25 基本搜索"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_bm25_db(tmpdir)
        cfg = Config(storage=StorageConfig(path=str(db_path)))
        searcher = Searcher(cfg)
        result = searcher.search("人工智能", limit=5)
        assert result["total"] >= 1
        assert result["query"] == "人工智能"
