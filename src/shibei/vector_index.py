"""HNSW 向量索引 — usearch 可选后端。

设计原则：
- SQLite 的 ``chunk_embeddings`` 是唯一事实源。
- 本模块的 ``vectors.usearch`` 只是派生缓存，文件损坏或缺失时可按数据库重建。
- usearch / numpy 不可用时，调用方回退到 BM25Index 的精确余弦检索。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import VectorConfig

logger = logging.getLogger(__name__)

INDEX_FILE = "vectors.usearch"
META_FILE = "vector_index.json"
EMB_TABLE = "chunk_embeddings"

_CACHE: dict[tuple[str, int, int, int, int], "HnswVectorIndex"] = {}
_CACHE_LOCK = threading.Lock()


def usearch_available() -> bool:
    """usearch 与 numpy 是否都可用。"""
    try:
        import numpy  # noqa: F401
        import usearch  # noqa: F401
    except ImportError:
        return False
    return True


def _unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


class HnswVectorIndex:
    """单库单维度的 HNSW 索引，key 使用 ``chunks.rowid``。"""

    def __init__(
        self,
        db_file: str | Path,
        *,
        dim: int,
        m: int = 16,
        ef_construction: int = 200,
        ef_search: int = 64,
        overfetch: int = 4,
    ):
        self.db_file = Path(db_file)
        self.index_path = self.db_file.parent / INDEX_FILE
        self.meta_path = self.db_file.parent / META_FILE
        self.dim = int(dim)
        self.m = max(2, int(m))
        self.ef_construction = max(1, int(ef_construction))
        self.ef_search = max(1, int(ef_search))
        self.overfetch = max(1, int(overfetch))
        self._index: Any = None
        self._lock = threading.RLock()

    # ---------- 元信息 ----------

    def _read_meta(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_meta(self, count: int) -> None:
        payload = {
            "version": 1,
            "dim": self.dim,
            "metric": "cos",
            "count": int(count),
            "m": self.m,
            "ef_construction": self.ef_construction,
            "ef_search": self.ef_search,
        }
        tmp = self.meta_path.with_name(
            f"{self.meta_path.name}.{os.getpid()}.tmp"
        )
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.meta_path)

    # ---------- 加载 / 重建 ----------

    def _new_index(self) -> Any:
        from usearch.index import Index

        return Index(
            ndim=self.dim,
            metric="cos",
            dtype="f32",
            connectivity=self.m,
            expansion_add=self.ef_construction,
            expansion_search=self.ef_search,
        )

    def _db_count(self) -> int:
        if not self.db_file.exists():
            return 0
        try:
            with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
                row = conn.execute(f"SELECT COUNT(*) FROM {EMB_TABLE}").fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row else 0

    def _load(self) -> bool:
        """只加载已存在的索引，不触发重建。"""
        if self._index is not None:
            return True
        if not self.index_path.exists():
            return False
        meta = self._read_meta()
        try:
            meta_dim = int(meta.get("dim", 0)) if meta else 0
        except (TypeError, ValueError):
            meta_dim = 0
        if meta_dim != self.dim:
            return False
        try:
            from usearch.index import Index

            index = Index.restore(str(self.index_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("HNSW 索引加载失败，将回退精确检索: %s", exc)
            return False
        if int(getattr(index, "ndim", 0)) != self.dim:
            logger.warning("HNSW 索引维度不匹配，将重建")
            return False
        self._index = index
        return True

    def ensure_loaded(self) -> bool:
        """确保索引可用；文件缺失或计数不一致时按数据库重建。"""
        with self._lock:
            if self._index is not None:
                return True
            if self._load():
                meta = self._read_meta() or {}
                try:
                    meta_count = int(meta.get("count", -1))
                except (TypeError, ValueError):
                    meta_count = -1
                if meta_count == self._db_count():
                    return True
            return self.rebuild()

    def rebuild(self) -> bool:
        """从 ``chunk_embeddings`` 全量重建，并原子替换索引文件。"""
        if not usearch_available():
            return False
        with self._lock:
            try:
                import numpy as np

                index = self._new_index()
                count = 0
                with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
                    cursor = conn.execute(
                        f"SELECT rowid, vector FROM {EMB_TABLE} ORDER BY rowid"
                    )
                    while True:
                        rows = cursor.fetchmany(1024)
                        if not rows:
                            break
                        keys = np.asarray([r[0] for r in rows], dtype=np.uint64)
                        vectors = np.asarray(
                            [_unpack_vector(r[1]) for r in rows], dtype=np.float32
                        )
                        index.add(keys, vectors)
                        count += len(rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("HNSW 索引重建失败，回退精确检索: %s", exc)
                self._index = None
                return False

            try:
                self._atomic_save(index)
                self._index = index
                self._write_meta(count)
            except Exception as exc:  # noqa: BLE001
                logger.warning("HNSW 索引写入失败，回退精确检索: %s", exc)
                self._index = None
                return False
            logger.info("HNSW 索引重建完成: %s 条", count)
            return True

    def _atomic_save(self, index: Any) -> None:
        tmp = self.index_path.with_name(
            f"{self.index_path.name}.{os.getpid()}.tmp"
        )
        index.save(str(tmp))
        os.replace(tmp, self.index_path)

    # ---------- 增删查 ----------

    def add(self, items: list[tuple[int, list[float]]]) -> bool:
        """写入向量。

        调用方应先把向量写入 SQLite；若索引文件缺失或计数不一致，
        直接从数据库重建（此时新向量已经包含在数据库里）。
        """
        if not items:
            return True
        if not usearch_available():
            return False
        with self._lock:
            if not self._load():
                return self.rebuild()

            meta = self._read_meta() or {}
            db_count = self._db_count()
            try:
                meta_count = int(meta.get("count", -1))
            except (TypeError, ValueError):
                meta_count = -1
            # 数据库比索引多出恰好这批向量：增量写入
            if meta_count < 0 or db_count - meta_count != len(items):
                # 被外部改动、重复 embedding 或计数异常，直接重建更稳
                return self.rebuild()

            try:
                import numpy as np

                assert self._index is not None
                keys = np.asarray([r[0] for r in items], dtype=np.uint64)
                vectors = np.asarray([r[1] for r in items], dtype=np.float32)
                try:
                    self._index.add(keys, vectors)
                except RuntimeError:
                    # usearch 不允许重复 key；覆盖场景先删后加
                    self._index.remove(keys)
                    self._index.add(keys, vectors)
                self._atomic_save(self._index)
                self._write_meta(int(self._index.size))
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("HNSW 增量写入失败，下次查询将重建: %s", exc)
                self.invalidate()
                return False

    def remove(self, keys: list[int]) -> bool:
        """删除 key；索引不存在时视为成功（数据库才是事实源）。"""
        if not keys:
            return True
        if not usearch_available():
            return False
        with self._lock:
            if not self._load():
                return True
            try:
                import numpy as np

                assert self._index is not None
                self._index.remove(np.asarray(keys, dtype=np.uint64))
                self._atomic_save(self._index)
                self._write_meta(int(self._index.size))
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("HNSW 删除失败，下次查询将重建: %s", exc)
                self.invalidate()
                return False

    def search(
        self,
        query_vec: list[float],
        limit: int,
        min_score: float = 0.0,
    ) -> list[tuple[int, float]] | None:
        """返回 ``[(rowid, cosine), ...]``；不可用时返回 None 交给调用方回退。"""
        if not query_vec or limit <= 0:
            return []
        if not self.ensure_loaded():
            return None
        try:
            import numpy as np

            assert self._index is not None
            matches = self._index.search(
                np.asarray(query_vec, dtype=np.float32),
                count=max(1, int(limit)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("HNSW 检索失败，回退精确检索: %s", exc)
            self.invalidate()
            return None

        hits = [
            (int(key), 1.0 - float(distance))
            for key, distance in zip(matches.keys, matches.distances)
        ]
        return [(rowid, sim) for rowid, sim in hits if sim >= min_score]

    def invalidate(self) -> None:
        """删除索引文件和元信息；下次查询时重建。"""
        with self._lock:
            self._index = None
            for path in (self.index_path, self.meta_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning("删除 HNSW 索引文件失败 (%s): %s", path, exc)


def get_shared_hnsw_index(
    db_file: str | Path,
    vector_config: "VectorConfig",
) -> HnswVectorIndex:
    """按数据库路径复用索引实例，避免每次搜索都重新加载大文件。"""
    resolved = str(Path(db_file).resolve())
    hnsw = vector_config.hnsw
    key = (
        resolved,
        int(vector_config.dim),
        int(hnsw.m),
        int(hnsw.ef_construction),
        int(hnsw.ef_search),
    )
    with _CACHE_LOCK:
        index = _CACHE.get(key)
        if index is None:
            index = HnswVectorIndex(
                resolved,
                dim=vector_config.dim,
                m=hnsw.m,
                ef_construction=hnsw.ef_construction,
                ef_search=hnsw.ef_search,
                overfetch=hnsw.overfetch,
            )
            _CACHE[key] = index
        return index


def invalidate_vector_index(db_file: str | Path) -> None:
    """删除某个数据库对应的索引文件和进程内缓存。"""
    resolved = str(Path(db_file).resolve())
    parent = Path(resolved).parent
    for name in (INDEX_FILE, META_FILE):
        try:
            (parent / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("删除 HNSW 索引文件失败 (%s): %s", parent / name, exc)
    with _CACHE_LOCK:
        for key in [k for k in _CACHE if k[0] == resolved]:
            entry = _CACHE.pop(key)
            entry._index = None
