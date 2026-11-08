"""统一召回内核 recall() 测试。"""

from pathlib import Path

import pytest

from shibei.bm25 import BM25Index
from shibei.config import Config, StorageConfig
from shibei.memory import MemoryStore
from shibei.recall import recall

LONG_TEXT = "面试重点考察 Redis 缓存与持久化，" * 40


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    BM25Index(str(tmp_path / "fts5.db")).add_batch([
        ("/redis.md", "/redis.md", "interview", LONG_TEXT, "", ""),
        ("/ai.md", "/ai.md", "ai", "人工智能正在改变世界。", "", ""),
    ])
    MemoryStore(tmp_path).add("用户偏好 Python", category="preference", tags="tech")
    return Config(storage=StorageConfig(path=str(tmp_path)))


def test_recall_raw_returns_envelope(cfg):
    out = recall(cfg, "Redis 缓存", mode="raw")
    assert out["schema_version"] == 1
    assert out["mode"] == "raw"
    assert out["total"] >= 1
    assert out["results"][0]["source"] == "/redis.md"
    # raw 模式不压缩：text 仍是 bm25 的 300 字截断
    assert len(out["results"][0]["text"]) <= 300


def test_recall_trace_has_stages_and_why(cfg):
    out = recall(cfg, "Redis 缓存", mode="raw", trace=True)
    stages = [stage["name"] for stage in out["trace"]["stages"]]
    assert "rewrite" in stages
    assert "expand" in stages
    assert "retrieve" in stages
    assert out["trace"]["total_ms"] >= 0

    why = out["results"][0]["why"]
    assert "content" in why["routes"] or "summary" in why["routes"]
    assert why["matched_terms"]


def test_recall_full_text_fetches_complete_chunk(cfg):
    out = recall(cfg, "Redis 缓存", mode="raw", full_text=True)
    assert len(out["results"][0]["text"]) > 300


def test_recall_pack_trims_and_adds_hints(cfg):
    out = recall(cfg, message="我喜欢用 Python 而不是 Go", mode="pack", write_hints=True)
    assert out["mode"] == "pack"
    assert out["total"] >= 0
    for item in out["results"]:
        assert len(item["text"]) <= 241
    assert out["suggest_remember"]["should"] is True
    assert out["persona"]["total"] >= 1


def test_recall_pack_respects_write_hints_false(cfg):
    out = recall(cfg, message="随便聊聊", mode="pack", write_hints=False)
    assert "suggest_remember" not in out


def test_recall_memories_can_be_disabled(cfg):
    with_memories = recall(cfg, "Python", mode="raw", memories=True)
    without = recall(cfg, "Python", mode="raw", memories=False)
    assert with_memories["memories"]
    assert without["memories"] == []


def test_recall_empty_query_returns_empty_envelope(cfg):
    out = recall(cfg, "", mode="raw", trace=True)
    assert out["total"] == 0
    assert out["results"] == []
    assert out["trace"]["stages"] == []


def test_recall_rejects_unknown_mode(cfg):
    with pytest.raises(ValueError):
        recall(cfg, "Redis", mode="nope")


def test_recall_mmr_dedupes_near_duplicates(cfg):
    # cfg 里有 Redis 长文与无关的 AI 文档；再加一条与 Redis 高度重复的片段
    from ragkit.mmr import mmr_select

    items = [
        {"text": "Redis 缓存穿透要用布隆过滤器", "score": 0.9},
        {"text": "Redis 缓存穿透要用布隆过滤器方案", "score": 0.88},
        {"text": "Kafka 分区与消费者组", "score": 0.5},
    ]
    picked = mmr_select(items, 2)
    assert len(picked) == 2
    assert picked[1]["text"].startswith("Kafka")

    out = recall(cfg, "Redis 缓存", mode="raw", mmr=True)
    assert out["total"] >= 1
    ranks = [item["rank"] for item in out["results"]]
    assert ranks == list(range(1, len(ranks) + 1))


def test_degraded_reports_missing_stages(cfg):
    from shibei.recall import _degraded

    notes = _degraded(
        cfg, {"rerank": True, "vector": True, "llm_rewrite": True}, {"results": []}
    )
    assert any("rerank" in note for note in notes)
    assert any("vector" in note for note in notes)
    assert any("llm_rewrite" in note for note in notes)
