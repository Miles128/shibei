"""知识库整理 consolidator 测试"""

from shibei.consolidator import _overlap_coefficient, _cluster_by_edges, _build_similarity_pairs


def test_overlap_coefficient():
    assert _overlap_coefficient({"a", "b"}, {"a", "b"}) == 1.0
    assert round(_overlap_coefficient({"a", "b", "c"}, {"a", "b"}), 3) == round(2 / 2, 3)
    assert _overlap_coefficient(set(), {"a"}) == 0.0


def test_build_similarity_pairs():
    chunks = [
        {"text": "Redis 持久化机制包括 RDB 和 AOF 两种方式"},
        {"text": "Redis 使用 RDB 和 AOF 做持久化存储"},
        {"text": "Python 是一门广泛使用的编程语言"},
    ]
    edges = _build_similarity_pairs(chunks, threshold=0.4)
    # 第一条和第二条应相似
    assert len(edges) >= 1
    assert any(i == 0 and j == 1 for i, j, _ in edges)


def test_cluster_by_edges():
    edges = [(0, 1, 0.8), (1, 2, 0.8)]
    clusters = _cluster_by_edges(4, edges)
    # 0-1-2 一个 cluster，3 单独一个
    assert [0, 1, 2] in clusters or [2, 1, 0] in clusters
    assert [3] in clusters


def test_consolidate_empty(tmp_path):
    from shibei.consolidator import consolidate
    result = consolidate(tmp_path, max_chunks=10)
    assert result["status"] == "empty"


def test_consolidate_clusters(tmp_path):
    """在没有真实 db 的情况下，通过 mock chunks 测试聚类逻辑。"""
    from shibei.consolidator import consolidate
    from unittest.mock import patch

    chunks = [
        {"source": "a.md", "text": "Redis 持久化机制包括 RDB 和 AOF 两种方式", "tags": ""},
        {"source": "b.md", "text": "Redis 使用 RDB 和 AOF 做持久化存储", "tags": ""},
        {"source": "c.md", "text": "Python 是一门流行的编程语言", "tags": ""},
    ]

    class DummyLLM:
        ready = True
        def summarize(self, text, max_length=100):
            return "合并摘要"

    with patch("shibei.consolidator.load_chunks", return_value=chunks):
        result = consolidate(tmp_path, threshold=0.4, min_cluster_size=2, llm_client=DummyLLM())

    assert result["status"] == "ok"
    assert result["total_chunks"] == 3
    assert result["cluster_count"] >= 1
    cluster = result["clusters"][0]
    assert cluster["size"] >= 2
    assert "a.md" in cluster["sources"] or "b.md" in cluster["sources"]


def test_consolidate_apply_writes_back(tmp_path):
    """apply=True 时删除原 chunk 并写入合并摘要。"""
    from shibei.consolidator import consolidate
    from shibei.bm25 import BM25Index

    db_path = tmp_path
    bm25 = BM25Index(str(db_path / "fts5.db"))
    bm25.add_batch([
        ("a.md", "a.md", "ai", "Redis 持久化机制包括 RDB 和 AOF 两种方式"),
        ("b.md", "b.md", "ai", "Redis 使用 RDB 和 AOF 做持久化存储"),
        ("c.md", "c.md", "ai", "Python 是一门流行的编程语言"),
    ])

    class DummyLLM:
        ready = True
        def summarize(self, text, max_length=100):
            return "Redis 使用 RDB 和 AOF 两种持久化机制。"

    result = consolidate(db_path, threshold=0.4, min_cluster_size=2,
                         llm_client=DummyLLM(), apply=True)

    assert result["status"] == "ok"
    assert result["applied"] is True
    assert result["removed"] >= 2
    assert result["inserted"] >= 1

    # 验证原 chunk 被删除：3 个原 chunk - 2 个被合并 + 1 个合并摘要 = 2
    assert bm25.count() == 2
    # 更直接的验证：存在合并来源
    sources = bm25.list_sources()
    assert any(s.startswith("__consolidated__") for s in sources)


def test_consolidate_apply_false_does_not_modify(tmp_path):
    """apply=False 时不修改数据库。"""
    from shibei.consolidator import consolidate
    from shibei.bm25 import BM25Index

    db_path = tmp_path
    bm25 = BM25Index(str(db_path / "fts5.db"))
    bm25.add_batch([
        ("a.md", "a.md", "ai", "Redis 持久化机制包括 RDB 和 AOF"),
        ("b.md", "b.md", "ai", "Redis 使用 RDB 和 AOF 做持久化存储"),
    ])

    class DummyLLM:
        ready = True
        def summarize(self, text, max_length=100):
            return "合并摘要"

    result = consolidate(db_path, threshold=0.4, min_cluster_size=2,
                         llm_client=DummyLLM(), apply=False)

    assert result["applied"] is False
    assert result["removed"] == 0
    assert bm25.count() == 2


def test_apply_consolidation_with_external_summary(tmp_path):
    """外部 Agent 提供摘要时可直接 apply，无需 LLM。"""
    from shibei.consolidator import apply_consolidation
    from shibei.bm25 import BM25Index

    db_path = tmp_path
    bm25 = BM25Index(str(db_path / "fts5.db"))
    bm25.add_batch([
        ("a.md", "a.md", "ai", "Redis 持久化机制包括 RDB 和 AOF 两种方式"),
        ("b.md", "b.md", "ai", "Redis 使用 RDB 和 AOF 做持久化存储"),
        ("c.md", "c.md", "ai", "Python 是一门流行的编程语言"),
    ])

    # 获取 rowids
    rowids = [r["_rowid"] for r in bm25.search("Redis", limit=10)]
    assert len(rowids) == 2

    result = apply_consolidation(db_path, clusters=[{
        "rowids": rowids,
        "summary": "Redis 使用 RDB 和 AOF 两种持久化机制。",
        "sources": ["a.md", "b.md"],
    }])

    assert result["applied"] is True
    assert result["removed"] == 2
    assert result["inserted"] == 1
    assert result["invalid_clusters"] == 0
    assert bm25.count() == 2
    sources = bm25.list_sources()
    assert any(s.startswith("__consolidated__") for s in sources)


def test_apply_consolidation_skips_invalid_clusters(tmp_path):
    """缺少 rowids 或 summary 的 cluster 应被跳过。"""
    from shibei.consolidator import apply_consolidation
    from shibei.bm25 import BM25Index

    db_path = tmp_path
    bm25 = BM25Index(str(db_path / "fts5.db"))
    bm25.add_batch([
        ("a.md", "a.md", "ai", "Redis 持久化机制包括 RDB 和 AOF"),
    ])

    result = apply_consolidation(db_path, clusters=[
        {"rowids": [], "summary": "空 rowids"},
        {"rowids": [1], "summary": ""},
        {"rowids": [1], "summary": "有效合并"},
    ])

    assert result["applied"] is True
    assert result["invalid_clusters"] == 2
    assert result["removed"] == 1
    assert result["inserted"] == 1
