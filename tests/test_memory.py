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
    # source 默认加入 tags，便于与文档知识库统一检索
    assert r["source"] == "agent"
    assert set(r["tags"].split(",")) == {"agent", "python", "tech"}
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
    old = store.add("用户喜欢的编程语言是 Python", category="user", supersede_threshold=0.4)
    new = store.add("用户喜欢的编程语言已改为 Go", category="user", supersede_threshold=0.4)

    assert new["supersedes"] == old["id"]
    old_updated = store.get(old["id"])
    assert old_updated["superseded_by"] == new["id"]

    # 默认搜索不返回已被替代的旧版本
    results = store.search("用户喜欢 编程语言", category="user")
    assert len(results) == 1
    assert results[0]["id"] == new["id"]


def test_list_exclude_superseded(store):
    old = store.add("用户喜欢的编程语言是 Python", category="user", supersede_threshold=0.4)
    store.add("用户喜欢的编程语言已改为 Go", category="user", supersede_threshold=0.4)

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
    store.add("用户偏好颜色是红色", category="user", supersede_threshold=0.95)
    store.add("用户偏好颜色是蓝色", category="user", supersede_threshold=0.95)
    store.add("项目代号是 Lumina", category="project", supersede_threshold=0.95)
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


def test_find_compressible_candidates(store):
    """find_compressible 只返回旧记忆候选，不调用 LLM。"""
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")

    store.add("旧记忆主题 A 的内容", category="user", created_at=old, supersede_threshold=0.95)
    store.add("旧记忆主题 B 的内容", category="user", created_at=old, supersede_threshold=0.95)
    store.add("新记忆主题 C 的内容", category="user", created_at=recent, supersede_threshold=0.95)

    candidates = store.find_compressible(max_age_days=30, keep_recent=0)
    assert len(candidates) == 2
    facts = {c["fact"] for c in candidates}
    assert "旧记忆主题 A 的内容" in facts
    assert "旧记忆主题 B 的内容" in facts


def test_find_compressible_keeps_recent(store):
    """find_compressible 会保留最近 keep_recent 条。"""
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    for i, topic in enumerate(["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]):
        store.add(f"旧记忆主题 {topic} 的内容", category="user", created_at=old, supersede_threshold=0.95)

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




def test_score_higher_means_more_relevant(store):
    """记忆 score 应单调递增于相关度（修复 memory.py score 反转）。"""
    store.add("用户喜欢使用 Python 做后端开发", tags="tech")
    store.add("用户喜欢使用 Go 做后端开发", tags="tech")
    results = store.search("Python 后端")
    assert len(results) >= 1
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_cleanup_expired_parses_iso_variants(store):
    """过期清理应按 datetime 语义比较：兼容省略时区/不同偏移的 ISO 格式。"""
    now = datetime.now(timezone.utc)
    store.add("已过期 1（带时区）", expires_at=(now - timedelta(days=1)).isoformat())
    store.add("已过期 2（无时区）", expires_at=(now - timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="seconds"))
    store.add("已过期 3（+08:00 偏移）", expires_at=(now - timedelta(days=1)).astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds"))
    store.add("未过期", expires_at=(now + timedelta(days=1)).isoformat())
    store.add("格式非法", expires_at="not-a-date")

    deleted = store.cleanup_expired(now=now)
    assert deleted == 3
    remaining = {m["fact"] for m in store.list(limit=10, include_superseded=True)}
    assert {"未过期", "格式非法"} <= remaining


def test_delete_with_like_wildcards_does_not_mismatch(store):
    """memory_id 中的 LIKE 通配符应被转义，防止前缀匹配误删多条。"""
    r1 = store.add("记忆 A", tags="t1")
    r2 = store.add("记忆 B", tags="t2")
    # 用带 % 的假前缀删除：不应误匹配任何真实 id
    assert store.delete("%" + r1["id"][4:8]) is False
    assert store.get(r1["id"]) is not None
    assert store.get(r2["id"]) is not None


# ---- P0/P1 新功能：溯源字段 + 画像层 ----

def test_source_message_ids_roundtrip(store):
    """remember 传入 source_message_ids 后，搜索/列表应能带出（溯源）。"""
    store.add("用户偏好 Python", category="preference", source_message_ids="msg-101,msg-102")
    rows = store.search("Python 偏好", limit=5)
    assert rows and rows[0]["source_message_ids"] == "msg-101,msg-102"
    listed = store.list(limit=10)
    assert listed[0]["source_message_ids"] == "msg-101,msg-102"


def test_source_message_ids_default_empty(store):
    """未传 source_message_ids 时默认为空字符串。"""
    r = store.add("用户偏好 Python", category="preference")
    row = store.get(r["id"])
    assert row["source_message_ids"] == ""


def test_persona_aggregates_by_category(store):
    """persona() 按类别聚合有效记忆，排除被替代的版本。"""
    a = store.add("用户喜欢 Python 而不是 Go", category="preference")
    store.add("用户目标是转行 AI 产品", category="preference")
    store.add("NoteAI 是主力产品", category="project")
    store.add("这条会被替代", category="user")
    store.add("用户喜欢 Python 而不是 Go（新版）", category="preference")  # Jaccard 高，替代 a
    p = store.persona()
    assert "preference" in p["categories"]
    assert "project" in p["categories"]
    # 被替代的旧事实不应出现在画像里
    all_facts = [f["fact"] for facts in p["categories"].values() for f in facts]
    assert "用户喜欢 Python 而不是 Go" not in all_facts
    assert p["total"] >= 3


def test_persona_limits(store):
    """per_category / max_facts 限制生效（每句两个独特词，避免 Jaccard≥0.7 触发 supersede）。"""
    pairs = [("咖啡", "提神"), ("跑步", "晨练"), ("读书", "历史"), ("旅行", "露营"),
             ("摄影", "夜景"), ("编程", "Python"), ("园艺", "多肉"), ("烹饪", "川菜"),
             ("音乐", "古典"), ("绘画", "水彩")]
    for a, b in pairs:
        store.add(f"用户喜欢{a}与{b}", category="preference")
    p = store.persona(per_category=3, max_facts=20)
    assert len(p["categories"]["preference"]) == 3
    assert p["total"] == 3
