"""Dreamer 模块测试 — 洞察与 housekeeping"""

import tempfile
from pathlib import Path

from shibei.dreamer import (
    dream,
    extract_keywords,
    find_cross_references,
)


def test_extract_keywords_basic():
    """关键词提取基本功能"""
    kws = extract_keywords("人工智能正在改变世界，大模型技术突破")
    assert len(kws) > 0
    # 至少包含一个中文关键词
    assert any(len(k) >= 2 for k in kws)


def test_extract_keywords_empty():
    """空文本返回空列表"""
    kws = extract_keywords("")
    assert kws == []


def test_find_cross_references_basic():
    """跨文档引用检测"""
    chunks = [
        {
            "source": "/a.md",
            "file_path": "a.md",
            "tags": "ai",
            "text": "人工智能",
            "keywords": ["人工智能", "大模型"],
        },
        {
            "source": "/b.md",
            "file_path": "b.md",
            "tags": "ai",
            "text": "大模型应用",
            "keywords": ["大模型", "应用"],
        },
    ]
    refs = find_cross_references(chunks)
    # "大模型" 是共享关键词
    assert len(refs) >= 1
    assert refs[0]["strength"] >= 1


def test_dream_empty_db():
    """空库 dream 返回 empty 状态"""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = dream(Path(tmpdir))
        assert result["status"] == "empty"


def test_dream_basic():
    """dream 返回完整洞察结果"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        from shibei.bm25 import BM25Index
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/a.md", "/a.md", "ai", "人工智能正在改变世界。大模型技术突破。", "", ""),
            ("/b.md", "/b.md", "ai", "人工智能应用广泛。大模型是核心。", "", ""),
        ])

        result = dream(db_path, format="full")
        assert result["status"] == "ok"
        assert "cross_references" in result
        assert "patterns" in result
        assert "insights" in result
        assert "stats" in result


def test_dream_housekeeping_default_runs():
    """默认 dream 会执行 housekeeping 发现任务"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        from shibei.bm25 import BM25Index
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/a.md", "/a.md", "ai", "人工智能正在改变世界。", "", ""),
            ("/b.md", "/b.md", "ai", "大模型技术突破。", "", ""),
        ])

        result = dream(db_path)
        assert result["status"] == "ok"
        assert "housekeeping" in result
        assert result["housekeeping"]["enabled"] is True
        assert "compressible_candidates" in result["housekeeping"]["memory"]
        assert "consolidation" in result["housekeeping"]


def test_dream_housekeeping_identifies_similar_chunks():
    """dream housekeeping 识别相似 chunk 候选，不自动 apply"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        from shibei.bm25 import BM25Index
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/a.md", "/a.md", "ai", "Redis 持久化包括 RDB 和 AOF 两种方式", "", ""),
            ("/b.md", "/b.md", "ai", "Redis 使用 RDB 和 AOF 做持久化存储", "", ""),
        ])

        result = dream(
            db_path,
            housekeeping={"consolidate_threshold": 0.3},
        )
        assert result["status"] == "ok"
        cons = result["housekeeping"]["consolidation"]
        assert cons.get("cluster_count", 0) >= 1
        # 不自动 apply，但启发式摘要已生成，供外部 Agent 参考
        cluster = cons["clusters"][0]
        assert cluster.get("summary") != ""


def test_dream_identifies_compressible_memories():
    """dream housekeeping 识别可压缩记忆候选，不自动压缩。"""
    from datetime import datetime, timezone, timedelta
    from shibei.memory import MemoryStore
    from shibei.bm25 import BM25Index

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        # dream 依赖 BM25 chunks 判断是否为空；至少保留一个 chunk
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/notes.md", "/notes.md", "ai", " dreaming 占位 chunk", "", ""),
        ])

        store = MemoryStore(db_path)
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        store.add("临时计划 A", category="user", created_at=old, supersede_threshold=2.0)
        store.add("临时计划 B", category="user", created_at=old, supersede_threshold=2.0)
        store.add("长期项目目标", category="user", created_at=old, supersede_threshold=2.0)

        result = dream(
            db_path,
            housekeeping={"compress_keep_recent": 0},
        )
        assert result["status"] == "ok"
        mem = result["housekeeping"]["memory"]
        assert mem["compressible_count"] == 3
        assert len(mem["compressible_candidates"]) == 3
