"""LLMQueryRewriter 测试 — 查询改写与 Searcher 集成"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from shibei.bm25 import BM25Index
from shibei.config import Config, LLMConfig, RewriterConfig, StorageConfig
from shibei.llm_rewriter import LLMQueryRewriter
from shibei.searcher import Searcher


def test_llm_rewrite_parses_terms():
    """LLM 输出被解析为空格分隔关键词"""
    client = MagicMock()
    client._api_key.return_value = "sk-test"
    client.chat.return_value = "Redis 缓存 面试"

    rewriter = LLMQueryRewriter(LLMConfig(), max_terms=8, client=client)
    result = rewriter.rewrite("讲讲我面试里的 Redis")

    assert result["applied"] is True
    assert result["terms"] == ["Redis", "缓存", "面试"]
    assert result["rewritten"] == "Redis 缓存 面试"
    assert result["error"] is None


def test_llm_rewrite_unavailable_without_key(monkeypatch):
    """无 API key 时降级为原查询"""
    monkeypatch.delenv("SHIBEI_LLM_API_KEY", raising=False)
    rewriter = LLMQueryRewriter(LLMConfig(), max_terms=8)
    result = rewriter.rewrite("银行风控")
    assert result["rewritten"] == "银行风控"
    assert result["applied"] is False
    assert result["error"] == "llm_unavailable"


def test_llm_rewrite_failure_falls_back():
    """LLM 抛错时降级为原查询"""
    client = MagicMock()
    client._api_key.return_value = "sk-test"
    client.chat.side_effect = RuntimeError("network down")

    rewriter = LLMQueryRewriter(LLMConfig(), max_terms=8, client=client)
    result = rewriter.rewrite("信贷风险管理")
    assert result["rewritten"] == "信贷风险管理"
    assert result["applied"] is False
    assert "network down" in (result["error"] or "")


def test_searcher_with_llm_rewrite(monkeypatch):
    """Searcher 集成 LLM 改写后能通过扩展词召回文档"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/redis.md", "/redis.md", "interview",
             "面试中重点考察 Redis 缓存与持久化。", "", ""),
        ])

        client = MagicMock()
        client._api_key.return_value = "sk-test"
        client.chat.return_value = "Redis 面试 缓存"

        # 注入假 client：通过 patch LLMQueryRewriter.__init__ 后的 rewrite
        original_init = LLMQueryRewriter.__init__

        def _patched_init(self, llm_cfg, max_terms=8, prompt="", client=None):
            original_init(self, llm_cfg, max_terms=max_terms, prompt=prompt, client=client)
            self._client = client or MagicMock(
                _api_key=MagicMock(return_value="sk-test"),
                chat=MagicMock(return_value="Redis 面试 缓存"),
            )

        monkeypatch.setattr(LLMQueryRewriter, "__init__", _patched_init)
        monkeypatch.setenv("SHIBEI_LLM_API_KEY", "sk-test")

        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
            rewriter=RewriterConfig(enabled=True, use_llm=True, max_terms=8, rules=[]),
            llm=LLMConfig(enabled=False),
        )
        result = Searcher(cfg).search("讲讲我面试里 Redis", limit=5)

        assert result["total"] >= 1
        assert "llm_rewrite" in result
        assert result["llm_rewrite"]["applied"] is True
        assert "Redis" in result["query_used"]
        texts = " ".join(r["text"] for r in result["results"])
        assert "Redis" in texts


def test_searcher_llm_rewrite_error_keeps_original_path(monkeypatch):
    """LLM 失败时搜索仍走规则/原文路径"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/a.md", "/a.md", "ai", "人工智能介绍。", "", ""),
        ])

        def _failing_init(self, llm_cfg, max_terms=8, prompt="", client=None):
            self.llm_cfg = llm_cfg
            self.max_terms = max_terms
            self.prompt = prompt
            mock = MagicMock()
            mock._api_key.return_value = "sk-test"
            mock.chat.side_effect = RuntimeError("boom")
            self._client = mock

        monkeypatch.setattr(LLMQueryRewriter, "__init__", _failing_init)
        monkeypatch.setenv("SHIBEI_LLM_API_KEY", "sk-test")

        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
            rewriter=RewriterConfig(enabled=True, use_llm=True, rules=[]),
        )
        result = Searcher(cfg).search("人工智能", limit=5)
        assert result["total"] >= 1
        assert result["llm_rewrite"]["applied"] is False
        assert result["query_used"] == "人工智能"
