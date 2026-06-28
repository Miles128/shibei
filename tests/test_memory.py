"""对话记忆 MemoryStore 测试"""

import pytest
import tempfile
from pathlib import Path

from shibei.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def test_add_and_get(store):
    r = store.add("用户喜欢使用 Python 做后端开发", category="preference", tags="tech,python")
    assert r["fact"] == "用户喜欢使用 Python 做后端开发"
    assert r["category"] == "preference"
    assert r["tags"] == "tech,python"
    assert r["id"]

    got = store.get(r["id"])
    assert got["fact"] == r["fact"]


def test_search(store):
    store.add("用户面试准备重点关注 Redis", category="user", tags="面试", supersede_threshold=0.95)
    store.add("用户面试准备重点关注 Kafka", category="user", tags="面试", supersede_threshold=0.95)
    store.add("用户喜欢喝咖啡", category="preference", supersede_threshold=0.95)

    results = store.search("面试 Redis")
    assert len(results) >= 1
    assert any("Redis" in r["fact"] for r in results)


def test_search_by_category(store):
    store.add("项目使用 PostgreSQL", category="project")
    store.add("用户喜欢 PostgreSQL", category="preference")

    results = store.search("PostgreSQL", category="project")
    assert len(results) == 1
    assert results[0]["category"] == "project"


def test_supersedes_chain(store):
    old = store.add("用户喜欢的编程语言是 Python", category="user", supersede_threshold=0.5)
    new = store.add("用户喜欢的编程语言已改为 Go", category="user", supersede_threshold=0.5)

    assert new["supersedes"] == old["id"]
    old_updated = store.get(old["id"])
    assert old_updated["superseded_by"] == new["id"]

    # 默认搜索不返回已被替代的旧版本
    results = store.search("用户喜欢 编程语言", category="user")
    assert len(results) == 1
    assert results[0]["id"] == new["id"]


def test_list_exclude_superseded(store):
    old = store.add("用户喜欢的编程语言是 Python", category="user", supersede_threshold=0.5)
    store.add("用户喜欢的编程语言已改为 Go", category="user", supersede_threshold=0.5)

    all_active = store.list()
    assert len(all_active) == 1
    assert all_active[0]["id"] != old["id"]

    all_with_old = store.list(include_superseded=True)
    assert len(all_with_old) == 2


def test_forget(store):
    r = store.add("待删除记忆", category="user")
    assert store.forget(r["id"]) is True
    assert store.get(r["id"]) is None
    assert store.forget(r["id"]) is False


def test_count(store):
    store.add("记忆1", category="user", supersede_threshold=0.95)
    store.add("记忆2", category="user", supersede_threshold=0.95)
    store.add("记忆3", category="project", supersede_threshold=0.95)
    assert store.count() == 3
    assert store.count("user") == 2
    assert store.count("project") == 1


def test_empty_search(store):
    assert store.search("") == []
    assert store.search("不存在的词") == []


def test_forget_unlinks_superseded(store):
    old = store.add("旧信息", category="user")
    mid = store.add("中信息", category="user")
    new = store.add("新信息", category="user")

    # 删除中间版本
    store.forget(mid["id"])
    old_updated = store.get(old["id"])
    assert old_updated["superseded_by"] == ""


def test_search_mode_bm25_fallback():
    """无 embedding 模型时，hybrid 模式应回退到 BM25。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)
        store.add("用户喜欢 Python 编程", category="preference", supersede_threshold=0.95)
        store.add("用户喜欢 Go 编程", category="preference", supersede_threshold=0.95)

        hybrid = store.search("Python", mode="hybrid")
        bm25 = store.search("Python", mode="bm25")
        assert len(hybrid) == len(bm25)
        assert hybrid[0]["fact"] == bm25[0]["fact"]

        vector = store.search("Python", mode="vector")
        assert vector == []
