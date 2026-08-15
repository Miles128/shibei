"""BM25 索引测试"""

import tempfile
from pathlib import Path

from shibei.bm25 import BM25Index
from shibei.tokenizer import tokenize_text, tokenize_query, jieba_available


def test_tokenize_chinese():
    """中文分词"""
    tokens = tokenize_text("你好世界").split()
    assert "你好" in tokens
    assert "世界" in tokens
    if not jieba_available():
        assert "好世" in tokens
        assert "你" in tokens


def test_tokenize_mixed():
    """中英混合分词 — 英文作为完整 token"""
    tokens = tokenize_text("AI大模型很强大").split()
    assert "AI" in tokens
    if jieba_available():
        assert "模型" in tokens
    else:
        assert "大模" in tokens
        assert "模型" in tokens


def test_tokenize_query():
    """查询分词使用短语匹配"""
    result = tokenize_query("银行风控")
    assert '"银行风控"' in result
    assert '"银行"' in result
    assert '"行风"' in result
    assert '"风控"' in result


def test_bm25_add_and_search():
    """BM25 增删查"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        bm25 = BM25Index(db_path)

        items = [
            ("src/a.md", "a.md", "ai", "人工智能正在改变世界"),
            ("src/b.md", "b.md", "finance", "银行风控系统升级"),
            ("src/c.md", "c.md", "ai", "大模型技术突破"),
        ]
        bm25.add_batch(items)

        # 搜索
        results = bm25.search("人工智能", limit=5)
        assert len(results) >= 1
        assert "人工智能" in results[0]["text"]

        # 带标签过滤
        results = bm25.search("模型", limit=5, tag="ai")
        assert all("ai" in r["tags"] for r in results)


def test_bm25_remove_sources():
    """删除指定 source"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        bm25 = BM25Index(db_path)

        items = [
            ("src/a.md", "a.md", "ai", "人工智能内容"),
            ("src/b.md", "b.md", "finance", "金融内容"),
        ]
        bm25.add_batch(items)

        bm25.remove_sources(["src/a.md"])

        results = bm25.search("人工智能", limit=5)
        assert len(results) == 0

        results = bm25.search("金融", limit=5)
        assert len(results) == 1


def test_bm25_list_sources():
    """列出已入库 source"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        bm25 = BM25Index(db_path)

        items = [
            ("src/a.md", "a.md", "ai", "内容A"),
            ("src/b.md", "b.md", "ai", "内容B"),
        ]
        bm25.add_batch(items)

        sources = bm25.list_sources()
        assert set(sources) == {"src/a.md", "src/b.md"}


def test_bm25_clear():
    """清空索引"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        bm25 = BM25Index(db_path)

        bm25.add_batch([("src/a.md", "a.md", "ai", "测试内容")])
        bm25.clear()

        results = bm25.search("测试", limit=5)
        assert len(results) == 0


def test_bm25_delete_by_rowids():
    """按 rowid 精确删除 chunk"""
    import sqlite3
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        bm25 = BM25Index(db_path)

        items = [
            ("src/a.md", "a.md", "ai", "人工智能内容"),
            ("src/b.md", "b.md", "finance", "金融内容"),
        ]
        bm25.add_batch(items)

        # 获取 rowid
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT rowid, source FROM chunks ORDER BY rowid").fetchall()
        rowid_a = next(r[0] for r in rows if r[1] == "src/a.md")

        bm25.delete_by_rowids([rowid_a])

        assert bm25.count() == 1
        results = bm25.search("人工智能", limit=5)
        assert len(results) == 0
        results = bm25.search("金融", limit=5)
        assert len(results) == 1


def test_bm25_summary_recall():
    """摘要索引召回：正文不含查询词，但摘要含查询词时应命中。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        bm25 = BM25Index(db_path)

        # 7-tuple: (source, file_path, tags, content, parent_text, parent_id, summary)
        items = [
            (
                "src/a.md", "a.md", "ai",
                "深度学习模型需要大量算力支持。",
                "", "",
                "神经网络训练依赖 GPU 加速。"
            ),
        ]
        bm25.add_batch(items)

        # 查询词只出现在摘要中
        results = bm25.search("GPU 加速", limit=5)
        assert len(results) == 1
        assert results[0]["source"] == "src/a.md"
        # 返回给 LLM 的仍然是原文
        assert "深度学习" in results[0]["text"]


# ---- 回归测试：score 方向 / 噪音查询 / 标签边界 / 事务化合并 ----

def _index(tmp_path, items):
    bm = BM25Index(str(tmp_path / "fts5.db"))
    bm.add_batch(items)
    return bm


def test_score_higher_means_more_relevant(tmp_path):
    """FTS5 rank 越负越相关：score 应单调递增于相关度（修复 bm25.py score 反转）。"""
    bm = _index(tmp_path, [
        ("a.md", "a.md", "ai", "Redis 缓存是面试高频题，缓存穿透与雪崩", "", ""),
        ("b.md", "b.md", "ai", "Redis 分布式锁实现", "", ""),
        ("c.md", "c.md", "finance", "银行风控模型验证", "", ""),
    ])
    results = bm.search("Redis 缓存")
    assert len(results) >= 2
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True), "score 未按相关度降序"
    assert results[0]["score"] > results[-1]["score"]


def test_noise_query_returns_empty(tmp_path):
    """纯噪音查询（如 '###'）分词后为空，应短路返回空列表而非 FTS5 语法错误。"""
    bm = _index(tmp_path, [("a.md", "a.md", "ai", "Redis 缓存", "", "")])
    assert tokenize_query("###") == ""
    assert bm.search("###") == []
    assert bm.search("---") == []


def test_tag_filter_exact_boundary(tmp_path):
    """tag 过滤应精确匹配（',ai,' 不命中 ',daily,'），避免 LIKE 子串误伤。"""
    bm = _index(tmp_path, [
        ("a.md", "a.md", "ai", "大模型相关内容", "", ""),
        ("d.md", "d.md", "daily", "今日日记", "", ""),
    ])
    hits = bm.search("内容", tag="ai")
    assert [h["_rowid"] for h in hits] == [1]
    hits = bm.search("日记", tag="daily")
    assert [h["_rowid"] for h in hits] == [2]


def test_replace_by_rowids_atomic(tmp_path):
    """replace_by_rowids 在同一事务内删除+插入，结果数 = 原数 - 删 + 插。"""
    bm = _index(tmp_path, [
        ("a.md", "a.md", "ai", "第一段内容", "", ""),
        ("b.md", "b.md", "ai", "第二段内容", "", ""),
    ])
    bm.replace_by_rowids([1, 2], [
        ("__consolidated__/t/1#a", "__consolidated__/t/1#a", "consolidated",
         "合并后的完整内容", "", "", ""),
    ])
    assert bm.count() == 1
    assert bm.existing_rowids([1, 2]) == set()
    # 合并内容可被检索到
    hits = bm.search("合并后的完整")
    assert len(hits) == 1
