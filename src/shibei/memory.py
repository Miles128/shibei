"""Agent 对话记忆存储。

提供 SQLite + FTS5 的长期记忆存储，支持：
- 写入事实记忆
- BM25 全文搜索
- 版本链（supersedes）：新事实替代旧事实时保留历史

记忆与文档知识库存放在同一个数据库（fts5.db），仅通过 source/tags 区分来源：
- 文档 chunk 的 source 为文件路径，tags 为导入时指定的标签
- 记忆的 source 默认为 "agent"，tags 可携带分类/来源信息

表结构：
    memories(rowid INTEGER PK AUTOINCREMENT, id TEXT UUID, fact, ...)
    memories_fts(rowid INTEGER PK, fact, tags, category)
"""

import uuid
import sqlite3
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .tokenizer import tokenize_text, tokenize_query

logger = logging.getLogger(__name__)


MEMORIES_TABLE = "memories"
FTS_TABLE = "memories_fts"


class AbstractMemoryStore(ABC):
    """对话记忆存储抽象接口。"""

    @abstractmethod
    def add(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: Optional[str] = None,
        supersede_threshold: float = 0.7,
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

    def __init__(self, db_path: Path):
        """
        Args:
            db_path: 数据库文件存放目录
        """
        self.db_dir = Path(db_path)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        # 与文档知识库共用同一个数据库文件，通过 source/tags 区分来源
        self.db_file = self.db_dir / "fts5.db"
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
                    superseded_by TEXT
                )
                """
            )
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

    def add(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: Optional[str] = None,
        supersede_threshold: float = 0.7,
        created_at: Optional[str] = None,
    ) -> dict:
        """写入一条记忆，并尝试建立版本链。"""
        fact = fact.strip()
        if not fact:
            raise ValueError("fact cannot be empty")

        memory_id = str(uuid.uuid4())
        created_at = created_at or self._now()

        # 默认来源标签：未指定时标记为 agent，并把 source 纳入 tags 便于统一检索
        source = (source or "").strip() or "agent"
        tag_set = {t.strip() for t in (tags or "").split(",") if t.strip()}
        tag_set.add(source)
        tags = ",".join(sorted(tag_set))

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
                 supersedes, superseded_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id, fact, category or "", source,
                    tags, created_at, expires_at or "",
                    supersedes or "", "",
                ),
            )
            rowid = cur.lastrowid
            # 同步 FTS
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
        """BM25 召回 + Jaccard token 重叠度寻找最相似记忆。"""
        candidates = self._search_bm25(
            fact, limit=max(limit * 3, 10), category=category
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
    ) -> list[dict]:
        """BM25 搜索记忆。"""
        query = query.strip()
        if not query:
            return []
        return self._search_bm25(query, limit, category, include_superseded)

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
            conn.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = ?", (rowid,))
            cur = conn.execute(
                f"DELETE FROM {MEMORIES_TABLE} WHERE rowid = ?", (rowid,)
            )
            conn.execute(
                f"UPDATE {MEMORIES_TABLE} SET superseded_by = '' WHERE superseded_by = ?",
                (memory_id,),
            )
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

    def cleanup_expired(self, now: Optional[datetime] = None) -> int:
        """删除已经过期的记忆，返回删除条数。"""
        if now is None:
            now = datetime.now(timezone.utc)
        now_str = now.isoformat(timespec="seconds")

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            rows = conn.execute(
                f"""
                SELECT rowid, id FROM {MEMORIES_TABLE}
                WHERE expires_at IS NOT NULL AND expires_at != ''
                  AND expires_at <= ?
                  AND (superseded_by IS NULL OR superseded_by = '')
                """,
                (now_str,),
            ).fetchall()

            deleted = 0
            for rowid, memory_id in rows:
                conn.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = ?", (rowid,))
                conn.execute(f"DELETE FROM {MEMORIES_TABLE} WHERE rowid = ?", (rowid,))
                deleted += 1
            conn.commit()
        return deleted

    def find_compressible(
        self,
        max_age_days: int = 30,
        keep_recent: int = 10,
    ) -> List[dict]:
        """找出可能需要压缩的旧记忆候选。

        返回列表（按创建时间倒序），每条包含 id/fact/tags/category/created_at。
        外部 Agent 判断生命周期后，可调用 update/update_batch 压缩。
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat(timespec="seconds")

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT id, fact, tags, category, created_at FROM {MEMORIES_TABLE}
                WHERE (superseded_by IS NULL OR superseded_by = '')
                  AND (created_at <= ?)
                ORDER BY created_at DESC
                """,
                (cutoff,),
            ).fetchall()

        candidates = rows[keep_recent:]
        return [
            {
                "id": r["id"],
                "fact": r["fact"],
                "tags": r["tags"],
                "category": r["category"],
                "created_at": r["created_at"],
            }
            for r in candidates
        ]

    def update(self, memory_id: str, fact: str, tags: 'str | None' = None) -> bool:
        """更新一条记忆的内容（用于外部 Agent 执行压缩/修正）。"""
        rowid = self._rowid_by_id(memory_id)
        if rowid is None:
            return False

        old = self.get(memory_id)
        if old is None:
            return False

        new_tags = tags if tags is not None else old.get("tags", "")
        category = old.get("category", "") or "user"

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            # 先删 FTS 旧索引
            conn.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = ?", (rowid,))
            conn.execute(
                f"""
                UPDATE {MEMORIES_TABLE}
                SET fact = ?, tags = ?
                WHERE rowid = ?
                """,
                (fact, new_tags, rowid),
            )
            conn.execute(
                f"INSERT INTO {FTS_TABLE}(rowid, fact, tags, category) VALUES (?, ?, ?, ?)",
                (rowid, tokenize_text(fact), tokenize_text(new_tags), category),
            )
            conn.commit()
        return True
