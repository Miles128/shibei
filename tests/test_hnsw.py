"""HNSW 向量索引测试。"""

from pathlib import Path

import pytest

from shibei.bm25 import BM25Index
from shibei.config import HnswConfig, VectorConfig, load_config
from shibei.vector_index import INDEX_FILE, META_FILE, invalidate_vector_index

pytest.importorskip("usearch")


def _vec_config(tmp_path: Path) -> VectorConfig:
    return VectorConfig(
        enabled=True,
        provider="local",
        dim=4,
        batch_size=4,
        min_score=0.5,
        add_new_rows=True,
        backend="hnsw",
        hnsw=HnswConfig(m=8, ef_construction=50, ef_search=20, overfetch=4),
    )


def _make_index(tmp_path: Path):
    cfg = _vec_config(tmp_path)
    bm25 = BM25Index(str(tmp_path / "fts5.db"), vector_config=cfg)
    rowids = bm25.add_batch([
        ("a.md", "a.md", "ai", "第一段内容", "", ""),
        ("b.md", "b.md", "finance", "第二段内容", "", ""),
        ("c.md", "c.md", "ai", "第三段内容", "", ""),
    ])
    bm25.store_embeddings([
        (rowids[0], [1.0, 0.0, 0.0, 0.0]),
        (rowids[1], [0.0, 1.0, 0.0, 0.0]),
        (rowids[2], [0.9, 0.1, 0.0, 0.0]),
    ])
    return bm25, rowids


def test_config_parses_hnsw(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
vector:
  enabled: true
  backend: hnsw
  dim: 4
  hnsw:
    m: 8
    ef_construction: 40
    ef_search: 16
    overfetch: 2
""",
        encoding="utf-8",
    )
    cfg = load_config(str(config_path))
    assert cfg.vector.backend == "hnsw"
    assert cfg.vector.hnsw.m == 8
    assert cfg.vector.hnsw.ef_search == 16
    assert cfg.vector.hnsw.overfetch == 2


def test_hnsw_add_search_remove(tmp_path):
    bm25, rowids = _make_index(tmp_path)
    index_path = tmp_path / INDEX_FILE
    assert index_path.exists()
    assert (tmp_path / META_FILE).exists()

    hits = bm25.search_embeddings([1.0, 0.0, 0.0, 0.0], limit=2)
    assert [rowid for rowid, _ in hits] == [rowids[0], rowids[2]]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-4)

    bm25.delete_by_rowids([rowids[0]])
    hits = bm25.search_embeddings([1.0, 0.0, 0.0, 0.0], limit=2)
    assert rowids[0] not in [rowid for rowid, _ in hits]
    assert hits[0][0] == rowids[2]


def test_hnsw_rebuilds_after_index_file_removed(tmp_path):
    bm25, rowids = _make_index(tmp_path)
    invalidate_vector_index(str(tmp_path / "fts5.db"))
    assert not (tmp_path / INDEX_FILE).exists()

    # 同一个 BM25Index 持有旧的 HNSW 实例，invalidate 后再次查询应重建
    hits = bm25.search_embeddings([1.0, 0.0, 0.0, 0.0], limit=1)
    assert hits[0][0] == rowids[0]
    assert (tmp_path / INDEX_FILE).exists()


def test_hnsw_tag_filter_and_exact_fallback(tmp_path):
    bm25, rowids = _make_index(tmp_path)

    hits = bm25.search_embeddings([1.0, 0.0, 0.0, 0.0], limit=1, tag="ai")
    assert hits[0][0] == rowids[0]

    # finance 只有 1 条，limit=2 时过滤后不足，走精确兜底
    hits = bm25.search_embeddings([1.0, 0.0, 0.0, 0.0], limit=2, tag="finance")
    assert [rowid for rowid, _ in hits] == [rowids[1]]


def test_candidate_rowids_use_exact_only(tmp_path):
    bm25, rowids = _make_index(tmp_path)
    hits = bm25.search_embeddings(
        [1.0, 0.0, 0.0, 0.0],
        limit=5,
        candidate_rowids={rowids[1]},
    )
    assert [rowid for rowid, _ in hits] == [rowids[1]]
    assert hits[0][1] == pytest.approx(0.0, abs=1e-4)


def test_replace_invalidates_hnsw(tmp_path):
    bm25, rowids = _make_index(tmp_path)
    bm25.replace_by_rowids(
        rowids,
        [("__consolidated__/a", "__consolidated__/a", "consolidated", "合并内容", "", "", "")],
    )
    assert not (tmp_path / INDEX_FILE).exists()
    # 新 chunk 暂无向量，查询应返回空而不是旧索引结果
    assert bm25.search_embeddings([1.0, 0.0, 0.0, 0.0], limit=5) == []
