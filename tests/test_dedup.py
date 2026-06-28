"""精确去重模块测试"""

import tempfile
from pathlib import Path

from shibei.dedup import (
    ChunkHashStore, content_hash, dedup_chunks,
)


def test_content_hash_normalized():
    """内容 hash 归一化：去除首尾空白后相同视为重复"""
    h1 = content_hash("  hello world  ")
    h2 = content_hash("hello world")
    assert h1 == h2


def test_content_hash_different():
    """不同内容 hash 不同"""
    h1 = content_hash("人工智能")
    h2 = content_hash("机器学习")
    assert h1 != h2


def test_dedup_chunks_empty():
    """空列表去重"""
    unique, skipped, new_hashes = dedup_chunks([], set())
    assert unique == []
    assert skipped == 0
    assert new_hashes == set()


def test_dedup_chunks_no_duplicates():
    """无重复时全部保留"""
    chunks = [
        ("src/a.md", "a.md", "ai", "人工智能内容"),
        ("src/b.md", "b.md", "finance", "金融内容"),
    ]
    unique, skipped, new_hashes = dedup_chunks(chunks, set())
    assert len(unique) == 2
    assert skipped == 0
    assert len(new_hashes) == 2


def test_dedup_chunks_with_internal_duplicates():
    """批次内部重复：保留首次出现"""
    chunks = [
        ("src/a.md", "a.md", "ai", "重复内容"),
        ("src/b.md", "b.md", "ai", "重复内容"),
        ("src/c.md", "c.md", "ai", "唯一内容"),
    ]
    unique, skipped, new_hashes = dedup_chunks(chunks, set())
    assert len(unique) == 2
    assert skipped == 1
    assert unique[0][0] == "src/a.md"  # 首次出现保留
    assert unique[1][0] == "src/c.md"


def test_dedup_chunks_against_existing():
    """与已入库 hash 对比去重"""
    existing = {content_hash("已入库内容")}
    chunks = [
        ("src/a.md", "a.md", "ai", "已入库内容"),  # 重复，跳过
        ("src/b.md", "b.md", "ai", "新内容"),       # 保留
    ]
    unique, skipped, new_hashes = dedup_chunks(chunks, existing)
    assert len(unique) == 1
    assert skipped == 1
    assert unique[0][0] == "src/b.md"
    assert len(new_hashes) == 1


def test_chunk_hash_store_persistence():
    """hash store 持久化"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)

        store1 = ChunkHashStore(db_path)
        h = content_hash("测试内容")
        store1.add(h, "src/a.md")
        store1.save()

        # 重新加载
        store2 = ChunkHashStore(db_path)
        assert store2.has(h)
        assert store2.all_hashes() == {h}


def test_chunk_hash_store_remove_by_source():
    """按 source 删除 hash"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = ChunkHashStore(Path(tmpdir))
        store.add(content_hash("内容1"), "src/a.md")
        store.add(content_hash("内容2"), "src/a.md")
        store.add(content_hash("内容3"), "src/b.md")

        removed = store.remove_by_source("src/a.md")
        assert removed == 2
        assert len(store.all_hashes()) == 1
