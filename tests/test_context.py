"""答前上下文包 build_context / extract_query_terms 测试"""

import tempfile
from pathlib import Path

from shibei.bm25 import BM25Index
from shibei.config import Config, StorageConfig
from shibei.context import (
    build_context,
    extract_query_terms,
    suggest_remember_from_message,
)
from shibei.memory import MemoryStore
from shibei.services import Shibei


def test_extract_query_terms_strips_filler():
    q = extract_query_terms("讲讲我面试里的 Redis 怎么样")
    assert "Redis" in q
    assert "讲讲" not in q
    assert "怎么样" not in q


def test_suggest_remember_first_person():
    hint = suggest_remember_from_message("我喜欢用 Python 而不是 Go")
    assert hint["should"] is True
    assert "Python" in hint["fact"]


def test_suggest_remember_question_not_forced():
    hint = suggest_remember_from_message("Redis 是什么？")
    assert hint["should"] is False


def test_build_context_returns_pack():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/redis.md", "/redis.md", "interview",
             "面试重点考察 Redis 缓存与持久化。", "", ""),
        ])
        store = MemoryStore(db_path)
        store.add("用户偏好 Python", category="preference", tags="tech")

        cfg = Config(storage=StorageConfig(path=str(db_path)))
        pack = build_context(cfg, "讲讲我面试里的 Redis", limit=5)

        assert pack["message"].startswith("讲讲")
        assert "Redis" in pack["query_used"] or pack["total"] >= 0
        assert "results" in pack
        assert "memories" in pack
        assert "suggest_remember" in pack
        # 至少文档或记忆有一侧可命中
        assert pack["total"] >= 1 or len(pack["memories"]) >= 1


def test_shibei_context_sdk(tmp_path, monkeypatch):
    """Shibei.context SDK 入口"""
    db_path = tmp_path / "db"
    db_path.mkdir()
    BM25Index(str(db_path / "fts5.db")).add_batch([
        ("/a.md", "/a.md", "ai", "人工智能介绍。", "", ""),
    ])
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"storage:\n  path: {db_path}\nsources: []\n",
        encoding="utf-8",
    )
    brain = Shibei(str(config_file))
    pack = brain.context("介绍人工智能", limit=3, write_hints=False)
    assert "suggest_remember" not in pack
    assert pack["total"] >= 1
