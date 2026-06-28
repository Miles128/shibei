"""Agent 对话记忆存储。

提供 SQLite + FTS5 的长期记忆存储，支持：
- 写入事实记忆
- BM25 + 向量语义搜索
- 版本链（supersedes）：新事实替代旧事实时保留历史

表结构：
    memories(rowid INTEGER PK AUTOINCREMENT, id TEXT UUID, fact, ...)
    memories_fts(rowid INTEGER PK, fact, tags, category)
"""

import uuid
import sqlite3
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .tokenizer import tokenize_text, tokenize_query

logger = logging.getLogger(__name__)


MEMORIES_TABLE = "memories"
FTS_TABLE = "memories_fts"


class AbstractMemoryStore(ABC):
    """对话记忆存储抽象接口。

    具体实现可基于 SQLite、PostgreSQL 等。
    """

    @abstractmethod
    def add(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: Optional[str] = None,
        supersede_threshold: float = 0.7,
        embedding_model: Optional[str] = None,
    ) -> dict:
        """写入一条记忆。"""
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = 5,
        category: Optional[str] = None,
    ) -> list[dict]:
        """搜索记忆。"""
        ...

    @abstractmethod
    def list(
        self,
        limit: int = 100,
        category: Optional[str] = None,
    ) -> list[dict]:
        """列出记忆。"""
        ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """删除一条记忆。"""
        ...


class MemoryStore(AbstractMemoryStore):
    """基于 SQLite 的对话记忆存储。"""

    def __init__(self, db_path: Path, embedding_model: Optional[str] = None):
        """
        Args:
            db_path: 数据库文件存放目录
            embedding_model: 默认使用的 embedding 模型名；提供后才会计算/比较向量
        """
        self.db_dir = Path(db_path)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = self.db_dir / "memories.db"
        self._embedding_model = embedding_model
        self._embedding_fn = None
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {MEMORIES_TABLE} (
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
                    embedding BLOB
                )
                """
            )
            # 注意：FTS5 的 rowid 必须是 INTEGER，所以 content_rowid 指向 memories.rowid
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
                    fact, tags, category,
                    content='{MEMORIES_TABLE}', content_rowid='rowid'
                )
                """
            )
            conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _embed(self, text: str) -> Optional[list[float]]:
        """计算文本的向量表示；未配置模型或失败时返回 None。"""
        if not self._embedding_model:
            return None
        if self._embedding_fn is None:
            try:
                from .embedding import get_embedding_function, EmbeddingConfig
                cfg = EmbeddingConfig(model=self._embedding_model, device="cpu")
                self._embedding_fn = get_embedding_function(cfg)
            except Exception as e:
                logger.warning("加载 embedding 模型失败: %s", e)
                return None
        try:
            return self._embedding_fn([text])[0]
        except Exception as e:
            logger.warning("计算 embedding 失败: %s", e)
            return None

    def add(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: Optional[str] = None,
        supersede_threshold: float = 0.7,
        embedding_model: Optional[str] = None,
    ) -> dict:
        """写入一条记忆，并尝试建立版本链。"""
        fact = fact.strip()
        if not fact:
            raise ValueError("fact cannot be empty")

        memory_id = str(uuid.uuid4())
        created_at = self._now()

        # 计算 embedding（如果模型名提供）
        model = embedding_model or self._embedding_model
        embedding_blob = None
        if model:
            vec = self._embed(fact)
            if vec is not None:
                embedding_blob = _serialize_vector(vec)

        # 查找最相似的现有记忆
        supersedes = None
        similar = self._find_most_similar(fact, category, limit=1)
        if similar:
            top = similar[0]
            score = top.get("score", 0.0)
            if score >= supersede_threshold:
                supersedes = top["id"]

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            cur = conn.execute(
                f"""
                INSERT INTO {MEMORIES_TABLE}
                (id, fact, category, source, tags, created_at, expires_at,
                 supersedes, superseded_by, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id, fact, category or "", source or "",
                    tags or "", created_at, expires_at or "",
                    supersedes or "", "", embedding_blob,
                ),
            )
            rowid = cur.lastrowid
            # 同步 FTS（fact 用中文分词后的 token 串）
            conn.execute(
                f"""
                INSERT INTO {FTS_TABLE} (rowid, fact, tags, category)
                VALUES (?, ?, ?, ?)
                """,
                (rowid, tokenize_text(fact), tokenize_text(tags or ""), category or ""),
            )
            # 更新被替代的记忆
            if supersedes:
                conn.execute(
                    f"UPDATE {MEMORIES_TABLE} SET superseded_by = ? WHERE id = ?",
                    (memory_id, supersedes),
                )
            conn.commit()

        return self.get(memory_id)

    def _find_most_similar(
        self, fact: str, category: Optional[str] = None, limit: int = 3
    ) -> list[dict]:
        """用向量相似度寻找最相似的现有记忆；无向量时回退到 BM25 + Jaccard。"""
        # 优先使用向量语义相似度
        vec_results = self._vector_search(fact, limit=max(limit * 3, 10), category=category)
        if vec_results:
            return sorted(vec_results, key=lambda x: x["score"], reverse=True)[:limit]

        # 回退：BM25 召回 + Jaccard token 重叠度
        candidates = self.search(
            fact, limit=max(limit * 3, 10), category=category, mode="bm25"
        )
        if not candidates:
            return []

        fact_tokens = set(tokenize_text(fact).split())
        for c in candidates:
            cand_tokens = set(tokenize_text(c["fact"]).split())
            union = fact_tokens | cand_tokens
            inter = fact_tokens & cand_tokens
            c["score"] = len(inter) / len(union) if union else 0.0

        return sorted(candidates, key=lambda x: x["score"], reverse=True)[:limit]

    def _rowid_by_id(self, memory_id: str) -> Optional[int]:
        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            row = conn.execute(
                f"SELECT rowid FROM {MEMORIES_TABLE} WHERE id = ?", (memory_id,)
            ).fetchone()
            return row[0] if row else None

    @staticmethod
    def _escape_fts_query(query: str) -> str:
        """把查询字符串转成 FTS5 可识别的 token 串。"""
        return tokenize_query(query)

    def search(
        self,
        query: str,
        limit: int = 5,
        category: Optional[str] = None,
        include_superseded: bool = False,
        mode: str = "hybrid",
    ) -> list[dict]:
        """搜索记忆；mode 可选 bm25 / vector / hybrid。"""
        query = query.strip()
        if not query:
            return []

        if mode == "bm25":
            return self._search_bm25(query, limit, category, include_superseded)
        if mode == "vector":
            return self._vector_search(query, limit, category, include_superseded)[:limit]

        # hybrid: RRF 融合 BM25 + 向量
        bm25_results = self._search_bm25(query, limit * 3, category, include_superseded)
        vec_results = self._vector_search(query, limit * 3, category, include_superseded)
        return self._rrf_fuse(bm25_results, vec_results, limit)

    def _search_bm25(
        self,
        query: str,
        limit: int,
        category: Optional[str] = None,
        include_superseded: bool = False,
    ) -> list[dict]:
        """BM25 搜索记忆。"""
        sql = f"""
            SELECT m.id, m.fact, m.category, m.source, m.tags, m.created_at,
                   m.expires_at, m.supersedes, m.superseded_by,
                   rank AS score
            FROM {FTS_TABLE} f
            JOIN {MEMORIES_TABLE} m ON m.rowid = f.rowid
            WHERE {FTS_TABLE} MATCH ?
        """
        params: list = [self._escape_fts_query(query)]

        if category:
            sql += " AND m.category = ?"
            params.append(category)

        if not include_superseded:
            sql += " AND (m.superseded_by IS NULL OR m.superseded_by = '')"

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            raw_rank = row["score"]
            # FTS5 rank 越小越相关；转换为 0~1 之间的 score
            if raw_rank is None:
                score = 0.0
            else:
                score = 1.0 / (1.0 + abs(float(raw_rank)))
            results.append({
                "id": row["id"],
                "fact": row["fact"],
                "category": row["category"],
                "source": row["source"],
                "tags": row["tags"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "supersedes": row["supersedes"],
                "superseded_by": row["superseded_by"],
                "score": score,
            })
        return results

    def _vector_search(
        self,
        query: str,
        limit: int,
        category: Optional[str] = None,
        include_superseded: bool = False,
    ) -> list[dict]:
        """基于已存储 embedding 的向量语义搜索；无 embedding 时返回空列表。"""
        query_vec = self._embed(query)
        if query_vec is None:
            return []

        sql = f"""
            SELECT id, fact, category, source, tags, created_at,
                   expires_at, supersedes, superseded_by, embedding
            FROM {MEMORIES_TABLE}
            WHERE embedding IS NOT NULL
        """
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if not include_superseded:
            sql += " AND (superseded_by IS NULL OR superseded_by = '')"

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        q_norm = _norm(query_vec)
        if q_norm == 0:
            return []

        results = []
        for row in rows:
            blob = row["embedding"]
            if not blob:
                continue
            vec = _deserialize_vector(blob)
            score = _cosine_similarity(query_vec, vec)
            if score is None:
                continue
            results.append({
                "id": row["id"],
                "fact": row["fact"],
                "category": row["category"],
                "source": row["source"],
                "tags": row["tags"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "supersedes": row["supersedes"],
                "superseded_by": row["superseded_by"],
                "score": score,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    @staticmethod
    def _rrf_fuse(
        bm25_results: list[dict], vec_results: list[dict], limit: int, k: int = 60
    ) -> list[dict]:
        """用 Reciprocal Rank Fusion 融合 BM25 和向量结果。"""
        rrf_scores: dict[str, float] = {}
        best_result: dict[str, dict] = {}

        for rank, r in enumerate(bm25_results, start=1):
            key = r["id"]
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in best_result or r["score"] > best_result[key]["score"]:
                best_result[key] = r

        for rank, r in enumerate(vec_results, start=1):
            key = r["id"]
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in best_result or r["score"] > best_result[key]["score"]:
                best_result[key] = r

        fused = sorted(
            best_result.values(),
            key=lambda x: rrf_scores[x["id"]],
            reverse=True,
        )[:limit]

        for r in fused:
            r["rrf_score"] = round(rrf_scores[r["id"]], 6)
        return fused

    def get(self, memory_id: str) -> Optional[dict]:
        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT * FROM {MEMORIES_TABLE} WHERE id = ?", (memory_id,)
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def list(
        self,
        limit: int = 100,
        category: Optional[str] = None,
        include_superseded: bool = False,
    ) -> list[dict]:
        sql = f"SELECT * FROM {MEMORIES_TABLE} WHERE 1=1"
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if not include_superseded:
            sql += " AND (superseded_by IS NULL OR superseded_by = '')"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def forget(self, memory_id: str) -> bool:
        """删除一条记忆及其 FTS 索引。"""
        return self.delete(memory_id)

    def delete(self, memory_id: str) -> bool:
        """删除一条记忆及其 FTS 索引（实现 AbstractMemoryStore 接口）。"""
        rowid = self._rowid_by_id(memory_id)
        if rowid is None:
            # 支持前 8 位 ID 匹配
            with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
                row = conn.execute(
                    f"SELECT rowid FROM {MEMORIES_TABLE} WHERE id LIKE ?",
                    (f"{memory_id}%",),
                ).fetchone()
                if row:
                    rowid = row[0]
                else:
                    return False

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            # 先删 FTS
            conn.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = ?", (rowid,))
            # 再删主表
            cur = conn.execute(
                f"DELETE FROM {MEMORIES_TABLE} WHERE rowid = ?", (rowid,)
            )
            # 如果这条记忆 superseded 了别的记忆，需要解链
            conn.execute(
                f"UPDATE {MEMORIES_TABLE} SET superseded_by = '' WHERE superseded_by = ?",
                (memory_id,),
            )
            # 也按前8位解链
            conn.execute(
                f"UPDATE {MEMORIES_TABLE} SET superseded_by = '' WHERE superseded_by LIKE ?",
                (f"{memory_id}%",),
            )
            conn.commit()
            return cur.rowcount > 0

    def count(self, category: Optional[str] = None) -> int:
        sql = f"SELECT COUNT(*) FROM {MEMORIES_TABLE} WHERE 1=1"
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " AND (superseded_by IS NULL OR superseded_by = '')"
        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else 0


def _serialize_vector(vec: list[float]) -> bytes:
    import struct
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_vector(blob: bytes) -> list[float]:
    import struct
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _norm(vec: list[float]) -> float:
    import math
    return math.sqrt(sum(v * v for v in vec))


def _cosine_similarity(a: list[float], b: list[float]) -> Optional[float]:
    import math
    if len(a) != len(b) or not a:
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = _norm(a)
    nb = _norm(b)
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)
