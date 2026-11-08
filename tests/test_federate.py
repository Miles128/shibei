"""跨库联邦召回测试。"""

from pathlib import Path

from shibei.bm25 import BM25Index
from shibei.config import Config, StorageConfig
from shibei.federate import recall_federated

SHARED = "共享片段：Redis 缓存穿透要用布隆过滤器。"


def make_config(root: Path, name: str, docs: 'list[tuple[str, str]]') -> Config:
    db = root / name
    db.mkdir(parents=True, exist_ok=True)
    index = BM25Index(str(db / "fts5.db"))
    index.add_batch([
        (source, source, "", text, "", "") for source, text in docs
    ])
    return Config(storage=StorageConfig(path=str(db)))


def test_federated_merges_and_dedupes(tmp_path):
    a = make_config(tmp_path, "a", [
        ("/shared.md", SHARED),
        ("/a-only.md", "A 库独有：Redis 主从复制。"),
    ])
    b = make_config(tmp_path, "b", [
        ("/shared.md", SHARED),
        ("/b-only.md", "B 库独有：Redis 集群分片。"),
    ])

    out = recall_federated([("a", a), ("b", b)], query="Redis 缓存", limit=5)

    assert out["federated"] is True
    assert out["brains"] == ["a", "b"]
    assert out["schema_version"] == 1
    assert all("brain" in item for item in out["results"])
    assert out["trace"]["federated"]["counts"]

    shared = [item for item in out["results"] if item["source"] == "/shared.md"]
    assert len(shared) == 1, "同一片段只应出现一次"
    assert sorted(shared[0]["brains"]) == ["a", "b"]
    assert shared[0]["score_kind"] == "rrf"
    # 双库都命中的片段，RRF 分应高于任一单库命中
    assert shared[0]["fused_score"] > max(
        item["fused_score"] for item in out["results"] if item is not shared[0]
    )


def test_federated_respects_limit_and_rank(tmp_path):
    a = make_config(tmp_path, "a", [(f"/a{i}.md", f"Redis 文档 {i}") for i in range(5)])
    b = make_config(tmp_path, "b", [(f"/b{i}.md", f"Redis 文档 {i}") for i in range(5)])

    out = recall_federated([("a", a), ("b", b)], query="Redis", limit=3)
    assert out["total"] == 3
    assert [item["rank"] for item in out["results"]] == [1, 2, 3]
    scores = [item["fused_score"] for item in out["results"]]
    assert scores == sorted(scores, reverse=True)


def test_federated_trace_lists_each_brain(tmp_path):
    a = make_config(tmp_path, "a", [("/a.md", "Redis A")])
    b = make_config(tmp_path, "b", [("/b.md", "Redis B")])

    out = recall_federated([("a", a), ("b", b)], query="Redis")
    stages = {stage["name"] for stage in out["trace"]["stages"]}
    assert stages == {"a", "b"}
    assert out["trace"]["federated"]["k"] == 60


def test_federated_requires_at_least_one_brain():
    try:
        recall_federated([], query="Redis")
    except ValueError as exc:
        assert "brain" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
