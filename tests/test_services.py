"""Shibei 服务层测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig
from shibei.services import Shibei


def test_remember_writes_fact():
    """remember 写入单条事实记忆"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
        )

        brain = Shibei.__new__(Shibei)
        brain.config = cfg

        result = brain.remember("我喜欢用 Python")
        assert result["fact"] == "我喜欢用 Python"
        assert result["source"] == "agent"
        assert "agent" in result["tags"]


# ---- P1 自动记忆抽取（monkeypatch LLMClient，默认关闭） ----

def test_auto_remember_disabled_by_default(tmp_path):
    """auto_memory.enabled=False 时 auto_remember 返回空、不写库。"""
    db_path = tmp_path / "db"
    db_path.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"storage:\n  path: {db_path}\nsources: []\n", encoding="utf-8")
    brain = Shibei(str(config_file))
    assert brain.auto_remember("我喜欢用 Python") == []
    assert brain.count_memories() == 0


def test_auto_remember_extracts_and_writes(tmp_path, monkeypatch):
    """开启后，LLM 返回的事实被写入记忆（source=auto、带溯源）。"""
    db_path = tmp_path / "db"
    db_path.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"storage:\n  path: {db_path}\nsources: []\n"
        f"auto_memory:\n  enabled: true\n  max_facts: 3\n",
        encoding="utf-8",
    )
    brain = Shibei(str(config_file))

    # 伪造 LLM 返回（L1 结构化 JSON）
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def chat(self, messages, **extra):
            return """[
              {"scene_name": "我在和四海聊技术栈", "memories": [
                {"content": "用户喜欢 Python", "type": "persona", "priority": 80, "confidence": 0.9},
                {"content": "用户计划转行 AI 产品", "type": "episodic", "priority": 85, "confidence": 0.8}
              ]}
            ]"""

    # auto_remember 内部 `from .llm import LLMClient`，patch 模块级符号即可生效
    monkeypatch.setattr("shibei.llm.LLMClient", FakeClient)
    written = brain.auto_remember("我喜欢用 Python，计划转行 AI 产品", source_message_ids="turn-1")
    assert len(written) == 2
    facts = {w["fact"] for w in written}
    assert "用户喜欢 Python" in facts
    assert "用户计划转行 AI 产品" in facts
    rows = brain.list_memories(limit=10)
    assert all(r["source"] == "auto" for r in rows)
    assert all(r["source_message_ids"] == "turn-1" for r in rows)
    assert {r["memory_type"] for r in rows} == {"persona", "episodic"}
    assert all(r["priority"] >= 80 for r in rows)
    assert all(r["scene_name"] == "我在和四海聊技术栈" for r in rows)
