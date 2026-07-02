"""对话记忆 MemoryStore 测试"""

import pytest
import tempfile
from datetime import datetime, timedelta, timezone
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


def test_cleanup_expired(store):
    """清理已过期的记忆。"""
    from datetime import datetime, timezone, timedelta
    store.add("长期有效", category="user")
    store.add("临时事项", category="user", expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())

    assert store.count() == 2
    deleted = store.cleanup_expired()
    assert deleted == 1
    assert store.count() == 1


def test_cleanup_expired_keeps_future(store):
    """未过期记忆不应被清理。"""
    from datetime import datetime, timezone, timedelta
    store.add("未来事项", category="user", expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())

    assert store.cleanup_expired() == 0
    assert store.count() == 1


def test_compress_old_skips_long_term_and_personal(store):
    """压缩旧记忆时跳过 long_term 和 personal。"""
    class DummyLLM:
        ready = True
        def classify_memory_lifespan(self, fact):
            if "临时" in fact:
                return "short_term"
            if "个人" in fact:
                return "personal"
            return "long_term"
        def summarize(self, text, max_length=100):
            return f"摘要：{text[:10]}"

    # 用旧日期写入
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    store.add("临时计划 A", category="user", created_at=old)
    store.add("个人身份核心", category="user", created_at=old)
    store.add("项目长期架构", category="project", created_at=old)

    result = store.compress_old(max_age_days=30, keep_recent=0, llm_client=DummyLLM())
    assert result["compressed"] == 1
    assert result["skipped"] == 2
    assert result["deleted"] == 0

    active = store.list()
    assert any("摘要" in r["fact"] for r in active)
    assert any("个人身份核心" == r["fact"] for r in active)
    assert any("项目长期架构" == r["fact"] for r in active)


def test_compress_old_deletes_when_summary_fails(store):
    """摘要失败时删除短期记忆。"""
    class DummyLLM:
        ready = True
        def classify_memory_lifespan(self, fact):
            return "short_term"
        def summarize(self, text, max_length=100):
            raise RuntimeError("mock failure")

    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    store.add("临时事项 X", category="user", created_at=old)

    result = store.compress_old(max_age_days=30, keep_recent=0, llm_client=DummyLLM())
    assert result["deleted"] == 1
    assert store.count() == 0


def test_compress_old_keeps_recent(store):
    """最近 keep_recent 条记忆应保留。"""
    class DummyLLM:
        ready = True
        def classify_memory_lifespan(self, fact): return "short_term"
        def summarize(self, text, max_length=100): return "摘要"

    for i in range(5):
        store.add(f"短期 {i}", category="user")

    result = store.compress_old(max_age_days=0, keep_recent=2, llm_client=DummyLLM())
    # 最近 2 条保留，前 3 条压缩
    assert result["compressed"] == 3
    assert store.count() == 5


def test_find_compressible_candidates(store):
    """find_compressible 只返回旧记忆候选，不调用 LLM。"""
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")

    store.add("旧记忆 A", category="user", created_at=old, supersede_threshold=0.95)
    store.add("旧记忆 B", category="user", created_at=old, supersede_threshold=0.95)
    store.add("新记忆 C", category="user", created_at=recent, supersede_threshold=0.95)

    candidates = store.find_compressible(max_age_days=30, keep_recent=0)
    assert len(candidates) == 2
    facts = {c["fact"] for c in candidates}
    assert "旧记忆 A" in facts
    assert "旧记忆 B" in facts


def test_find_compressible_keeps_recent(store):
    """find_compressible 会保留最近 keep_recent 条。"""
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    for i in range(5):
        store.add(f"旧记忆 {i} 的内容 {i}", category="user", created_at=old, supersede_threshold=0.95)

    candidates = store.find_compressible(max_age_days=30, keep_recent=2)
    assert len(candidates) == 3


def test_update_memory(store):
    """update 可修改记忆内容并同步 FTS。"""
    r = store.add("原始内容", category="user", tags="old")
    assert store.update(r["id"], "压缩后内容", tags="old,compressed") is True

    updated = store.get(r["id"])
    assert updated["fact"] == "压缩后内容"
    assert updated["tags"] == "old,compressed"

    # FTS 索引已更新
    results = store.search("压缩后")
    assert len(results) == 1
    assert results[0]["id"] == r["id"]


