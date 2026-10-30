"""L1 结构化记忆字段测试（memory_type / priority / scene_name / confidence）。"""

import sqlite3

from shibei.memory import MemoryStore


def test_add_and_read_l1_fields(tmp_path):
    store = MemoryStore(tmp_path)
    r = store.add(
        "用户决定用 dsh 原生 workflow 脚本替代自研 Skill DAG",
        category="project",
        memory_type="instruction",
        priority=90,
        scene_name="我在和四海做 Lumina 的 dsh 迁移",
        confidence=0.9,
        source_message_ids="session-1:turn-3",
        supersede_threshold=0.99,
    )
    assert r["memory_type"] == "instruction"
    assert r["priority"] == 90
    assert r["scene_name"] == "我在和四海做 Lumina 的 dsh 迁移"
    assert r["confidence"] == 0.9
    assert r["source_message_ids"] == "session-1:turn-3"

    got = store.get(r["id"])
    assert got["memory_type"] == "instruction"
    assert got["priority"] == 90


def test_search_returns_l1_fields(tmp_path):
    store = MemoryStore(tmp_path)
    store.add(
        "用户偏好中文回答，先给结论",
        category="preference",
        memory_type="persona",
        priority=80,
        confidence=0.85,
        supersede_threshold=0.99,
    )
    hits = store.search("中文 结论")
    assert hits
    assert hits[0]["memory_type"] == "persona"
    assert hits[0]["priority"] == 80
    assert hits[0]["confidence"] == 0.85


def test_update_l1_only_touches_fields(tmp_path):
    store = MemoryStore(tmp_path)
    row = store.add("用户喜欢简洁回答", category="preference", supersede_threshold=0.99)
    assert store.update_l1(
        row["id"], memory_type="persona", priority=80, scene_name="偏好", confidence=0.7
    )
    got = store.get(row["id"])
    assert got["fact"] == "用户喜欢简洁回答"
    assert got["memory_type"] == "persona"
    assert got["priority"] == 80
    assert got["scene_name"] == "偏好"
    assert got["confidence"] == 0.7


def test_explicit_supersedes_id(tmp_path):
    store = MemoryStore(tmp_path)
    old = store.add("用户喜欢 Python", supersede_threshold=0.99)
    new = store.add("用户喜欢 Go", supersedes_id=old["id"], supersede_threshold=0.99)
    assert new["supersedes"] == old["id"]
    assert store.get(old["id"])["superseded_by"] == new["id"]


def test_migration_adds_l1_columns(tmp_path):
    db_file = tmp_path / "fts5.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            CREATE TABLE memories (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                fact TEXT NOT NULL,
                category TEXT,
                source TEXT,
                tags TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                supersedes TEXT,
                superseded_by TEXT,
                source_message_ids TEXT DEFAULT ''
            )
            """
        )
        conn.commit()

    MemoryStore(tmp_path)
    with sqlite3.connect(db_file) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
    assert {"memory_type", "priority", "scene_name", "confidence"} <= cols
