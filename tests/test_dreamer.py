"""Dreamer 模块测试 — 持久化与增量对比"""

import json
import tempfile
from pathlib import Path

from shibei.dreamer import (
    dream, save_dream, load_last_dream, list_dreams, _diff_dreams,
    extract_keywords, find_cross_references,
    compress_dreams, clean_old_dreams,
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
        result = dream(Path(tmpdir), persist=False, compare=False)
        assert result["status"] == "empty"


def test_save_and_load_dream():
    """dream 结果持久化与加载"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        result = {
            "status": "ok",
            "timestamp": "2026-06-18T12:00:00",
            "stats": {"total_chunks": 10, "unique_docs": 5,
                      "cross_references": 2, "topics_with_connections": 3},
            "cross_references": [],
            "patterns": {"ai": ["大模型"]},
            "insights": ["测试洞察"],
        }
        save_dream(db_path, result)

        # 加载最近一次
        loaded = load_last_dream(db_path)
        assert loaded is not None
        assert loaded["timestamp"] == "2026-06-18T12:00:00"
        assert loaded["stats"]["total_chunks"] == 10


def test_list_dreams():
    """列出历史 dream 记录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        # 保存两次
        save_dream(db_path, {
            "status": "ok", "timestamp": "2026-06-17T10:00:00",
            "stats": {"total_chunks": 5, "unique_docs": 3,
                      "cross_references": 1, "topics_with_connections": 2},
            "cross_references": [], "patterns": {}, "insights": [],
        })
        save_dream(db_path, {
            "status": "ok", "timestamp": "2026-06-18T12:00:00",
            "stats": {"total_chunks": 10, "unique_docs": 5,
                      "cross_references": 2, "topics_with_connections": 3},
            "cross_references": [], "patterns": {}, "insights": [],
        })

        dreams = list_dreams(db_path)
        # latest.json 不算
        assert len(dreams) == 2
        # 倒序，最新的在前
        assert dreams[0]["timestamp"] == "2026-06-18T12:00:00"


def test_diff_dreams():
    """两次 dream 结果对比"""
    prev = {
        "cross_references": [
            {"doc_a": "a.md", "doc_b": "b.md", "shared_keywords": ["AI"], "strength": 1}
        ],
        "patterns": {"ai": ["大模型", "机器学习"]},
        "insights": ["旧洞察"],
        "stats": {"total_chunks": 10, "unique_docs": 5,
                  "cross_references": 1, "topics_with_connections": 1},
    }
    curr = {
        "cross_references": [
            {"doc_a": "a.md", "doc_b": "b.md", "shared_keywords": ["AI"], "strength": 1},
            {"doc_a": "c.md", "doc_b": "d.md", "shared_keywords": ["金融"], "strength": 1},
        ],
        "patterns": {"ai": ["大模型", "深度学习"]},
        "insights": ["新洞察"],
        "stats": {"total_chunks": 15, "unique_docs": 7,
                  "cross_references": 2, "topics_with_connections": 2},
    }
    diff = _diff_dreams(prev, curr)

    assert diff["stats_delta"]["chunks"] == 5
    assert diff["stats_delta"]["docs"] == 2
    assert diff["stats_delta"]["cross_refs"] == 1
    assert "c.md ←→ d.md" in diff["new_connections"]
    assert "深度学习" in diff["new_topics"][0]
    assert "新洞察" in diff["new_insights"]
    assert "机器学习" in diff["lost_topics"][0]


def test_dream_with_persist_and_compare():
    """dream 集成持久化与对比"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        # 首次 dream（空库）
        result1 = dream(db_path, persist=True, compare=True)
        assert result1["status"] == "empty"
        # 空库不持久化，所以 latest.json 不存在
        assert not (db_path / "dreams" / "latest.json").exists()

        # 手动构造上次结果，模拟有数据
        save_dream(db_path, {
            "status": "ok",
            "timestamp": "2026-06-17T10:00:00",
            "stats": {"total_chunks": 5, "unique_docs": 3,
                      "cross_references": 1, "topics_with_connections": 2},
            "cross_references": [
                {"doc_a": "a.md", "doc_b": "b.md",
                 "shared_keywords": ["AI"], "strength": 1}
            ],
            "patterns": {"ai": ["AI"]},
            "insights": ["旧洞察"],
        })

        # 再次 dream（仍空库，但能对比）
        result2 = dream(db_path, persist=True, compare=True)
        # 空库返回 empty，不触发对比逻辑
        assert result2["status"] == "empty"


def test_compress_dreams():
    """压缩历史 dream：保留最近 N 次，更早的只存摘要"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)

        # 创建 8 条 dream 记录
        for i in range(8):
            save_dream(db_path, {
                "status": "ok",
                "timestamp": f"2026-06-{10+i:02d}T10:00:00",
                "stats": {"total_chunks": 10 + i, "unique_docs": 5,
                          "cross_references": 2, "topics_with_connections": 3},
                "cross_references": [{"doc_a": "a.md", "doc_b": "b.md",
                                      "shared_keywords": ["AI"], "strength": 1}],
                "patterns": {"ai": ["大模型"]},
                "insights": [f"洞察 {i}"],
            })

        # 压缩，保留最近 3 条
        result = compress_dreams(db_path, keep_recent=3)
        assert result["total"] == 8
        assert result["kept_full"] == 3
        assert result["compressed"] == 5

        # 检查：最近 3 条保持完整，更早的 5 条被压缩
        dreams = list_dreams(db_path)
        # list_dreams 返回摘要信息，compressed 字段应标记
        compressed_count = sum(1 for d in dreams if d.get("compressed"))
        assert compressed_count == 5


def test_clean_old_dreams():
    """清理超过 N 天的 dream 记录"""
    import os
    import time

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        dreams_dir = db_path / "dreams"
        dreams_dir.mkdir(parents=True)

        # 创建一个旧文件（修改时间为 100 天前）
        old_file = dreams_dir / "old.json"
        old_file.write_text('{"timestamp": "old"}')
        old_time = time.time() - 100 * 86400  # 100 天前
        os.utime(old_file, (old_time, old_time))

        # 创建一个新文件
        new_file = dreams_dir / "new.json"
        new_file.write_text('{"timestamp": "new"}')

        # 清理 90 天前的
        result = clean_old_dreams(db_path, max_age_days=90)
        assert result["deleted"] == 1
        assert result["remaining"] == 1
        assert not old_file.exists()
        assert new_file.exists()


def test_dream_brief_format():
    """dream brief 格式只返回精简内容（用 BM25 构造真实 chunk）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        from shibei.bm25 import BM25Index
        bm25 = BM25Index(str(db_path / "fts5.db"))
        # 两个文档共享 "人工智能" 关键词，保证有 cross_references
        bm25.add_batch([
            ("/a.md", "/a.md", "ai", "人工智能正在改变世界。大模型技术突破。", "", ""),
            ("/b.md", "/b.md", "ai", "人工智能应用广泛。大模型是核心。", "", ""),
        ])

        from shibei.dreamer import dream
        # full 格式
        full = dream(db_path, collection_name="test", format="full",
                     persist=False, compare=False)
        assert full["status"] == "ok"
        assert "cross_references" in full
        assert "patterns" in full
        assert "insights" in full

        # brief 格式
        brief = dream(db_path, collection_name="test", format="brief",
                      persist=False, compare=False)
        assert brief["status"] == "ok"
        assert "stats" in brief
        assert "insights" in brief
        assert "cross_references" not in brief
        assert "patterns" not in brief


def test_dream_llm_enhancement():
    """LLM 启用时 dream 会追加 [LLM] 洞察"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        from shibei.bm25 import BM25Index
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/a.md", "/a.md", "ai", "人工智能正在改变世界。", "", ""),
            ("/b.md", "/b.md", "ai", "大模型技术突破。", "", ""),
        ])

        from shibei.llm import LLMConfig, LLMClient
        from shibei.dreamer import dream

        llm = LLMClient(LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test"))
        llm.chat = lambda prompt, system_prompt=None: '["跨文档共同指向 AI 主题"]'

        result = dream(db_path, collection_name="test", format="brief",
                       persist=False, compare=False, llm_client=llm)
        assert result["status"] == "ok"
        assert any("[LLM]" in ins for ins in result["insights"])


def test_dream_llm_failure_graceful_degrade():
    """LLM 调用失败时不影响基础洞察"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        from shibei.bm25 import BM25Index
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/a.md", "/a.md", "ai", "人工智能正在改变世界。", "", ""),
            ("/b.md", "/b.md", "ai", "大模型技术突破。", "", ""),
        ])

        from shibei.llm import LLMConfig, LLMClient
        from shibei.dreamer import dream

        llm = LLMClient(LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test"))
        llm.chat = lambda prompt, system_prompt=None: (_ for _ in ()).throw(
            RuntimeError("mock failure")
        )

        result = dream(db_path, collection_name="test", format="brief",
                       persist=False, compare=False, llm_client=llm)
        assert result["status"] == "ok"
        assert len(result["insights"]) > 0
        assert not any("[LLM]" in ins for ins in result["insights"])
