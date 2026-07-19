"""QueryRewriter 测试 — 查询改写与 Searcher 集成"""

import tempfile
from pathlib import Path

from shibei.config import Config, RewriterConfig, RewriteRule, StorageConfig
from shibei.rewriter import QueryRewriter
from shibei.searcher import Searcher


def test_rewrite_basic():
    """基本关键词改写"""
    rewriter = QueryRewriter([
        RewriteRule(source="讲讲", target="介绍"),
        RewriteRule(source="怎么看", target="分析"),
    ])
    result = rewriter.rewrite("讲讲人工智能怎么看")
    assert result["original"] == "讲讲人工智能怎么看"
    assert result["rewritten"] == "介绍人工智能分析"
    assert {"from": "讲讲", "to": "介绍"} in result["applied"]
    assert {"from": "怎么看", "to": "分析"} in result["applied"]


def test_rewrite_no_match_returns_original():
    """无匹配规则时返回原查询"""
    rewriter = QueryRewriter([
        RewriteRule(source="讲讲", target="介绍"),
    ])
    result = rewriter.rewrite("数据库索引优化")
    assert result["original"] == "数据库索引优化"
    assert result["rewritten"] == "数据库索引优化"
    assert result["applied"] == []


def test_rewrite_disabled_rule_ignored():
    """禁用规则不参与改写"""
    rewriter = QueryRewriter([
        RewriteRule(source="讲讲", target="介绍", enabled=False),
        RewriteRule(source="怎么看", target="分析"),
    ])
    result = rewriter.rewrite("讲讲怎么看")
    assert result["rewritten"] == "讲讲分析"
    assert result["applied"] == [{"from": "怎么看", "to": "分析"}]


def test_rewrite_empty_rules():
    """空规则列表直接返回原查询"""
    rewriter = QueryRewriter([])
    result = rewriter.rewrite("任意查询")
    assert result["rewritten"] == "任意查询"
    assert result["applied"] == []


def test_rewrite_empty_query():
    """空查询返回空结果"""
    rewriter = QueryRewriter([RewriteRule(source="讲讲", target="介绍")])
    result = rewriter.rewrite("  ")
    assert result["original"] == ""
    assert result["rewritten"] == ""
    assert result["applied"] == []


def test_rewrite_for_bm25():
    """rewrite_query_for_bm25 直接返回字符串"""
    rewriter = QueryRewriter([
        RewriteRule(source="讲讲", target="介绍"),
    ])
    assert rewriter.rewrite_query_for_bm25("讲讲机器学习") == "介绍机器学习"


def test_searcher_with_rewriter():
    """Searcher 集成 rewriter 后能通过改写召回文档"""
    from shibei.bm25 import BM25Index

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/intro.md", "/intro.md", "ai", "人工智能介绍。", "", ""),
            ("/analysis.md", "/analysis.md", "ai", "金融风险分析。", "", ""),
        ])

        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
            rewriter=RewriterConfig(
                enabled=True,
                rules=[
                    RewriteRule(source="讲讲", target="介绍"),
                    RewriteRule(source="怎么看", target="分析"),
                ],
            ),
        )
        searcher = Searcher(cfg)

        # 口语 "讲讲人工智能" 改写为 "介绍人工智能"，应召回包含 "介绍" 的文档
        result = searcher.search("讲讲人工智能", limit=5)
        assert result["total"] >= 1
        assert "rewrite" in result
        assert result["rewrite"]["rewritten"] == "介绍人工智能"
        texts = " ".join(r["text"] for r in result["results"])
        assert "介绍" in texts
