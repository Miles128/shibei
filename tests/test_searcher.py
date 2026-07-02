"""Searcher 测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig
from shibei.searcher import Searcher
from shibei.llm import LLMConfig, LLMClient


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
        assert result["original_query"] == "人工智能"
        assert result["rewritten_query"] == "人工智能"


def test_searcher_llm_rewrite_query():
    """LLM 启用时会改写查询并用改写结果搜索"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_bm25_db(tmpdir)
        cfg = Config(storage=StorageConfig(path=str(db_path)))

        llm = LLMClient(LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test"))
        llm.chat = lambda prompt, system_prompt=None: "人工智能"

        searcher = Searcher(cfg, llm_client=llm)
        result = searcher.search("AI", limit=5)

        assert result["original_query"] == "AI"
        assert result["rewritten_query"] == "人工智能"
        assert result["total"] >= 1


def test_searcher_llm_rewrite_failure_degrade():
    """LLM 改写失败时回退到原查询"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = _make_bm25_db(tmpdir)
        cfg = Config(storage=StorageConfig(path=str(db_path)))

        llm = LLMClient(LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test"))
        llm.chat = lambda prompt, system_prompt=None: (_ for _ in ()).throw(
            RuntimeError("mock failure")
        )

        searcher = Searcher(cfg, llm_client=llm)
        result = searcher.search("人工智能", limit=5)

        assert result["original_query"] == result["rewritten_query"]
        assert result["total"] >= 1
