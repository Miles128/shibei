"""BM25 索引测试"""

import tempfile
from pathlib import Path

from shibei.bm25 import BM25Index
from shibei.tokenizer import tokenize_text, tokenize_query


def test_tokenize_chinese():
    """中文 bigram + 单字分词"""
    tokens = tokenize_text("你好世界").split()
    assert "你好" in tokens
    assert "好世" in tokens
    assert "世界" in tokens
    assert "你" in tokens


def test_tokenize_mixed():
    """中英混合分词 — 英文作为完整 token，中文 bigram"""
    tokens = tokenize_text("AI大模型很强大").split()
    assert "AI" in tokens
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
