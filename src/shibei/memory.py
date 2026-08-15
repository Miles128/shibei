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


def _escape_like(s: str) -> str:
    """转义 LIKE 模式中的通配符（% _ \），防止前缀匹配误伤。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_dt(s: str) -> Optional[datetime]:
    """解析 ISO 8601 字符串为带时区的 datetime；无法解析返回 None。"""
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        # 无时区信息视为 UTC（与写入端一致）
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
            # 自动迁移：旧库补 source_message_ids 列（溯源字段）
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({MEMORIES_TABLE})").fetchall()}
            if "source_message_ids" not in cols:
                conn.execute(
                    f"ALTER TABLE {MEMORIES_TABLE} ADD COLUMN source_message_ids TEXT DEFAULT ''"
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
        source_message_ids: str = "",
    ) -> dict:
        """写入一条记忆，并尝试建立版本链。

        Args:
            source_message_ids: 溯源字段，宿主传入触发该记忆的原始消息 ID
                （对标 TencentDB L1 原子记忆的 source_message_ids 溯源链），
                召回时随记忆返回，Agent 可回溯到原始对话。
        """
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
                 supersedes, superseded_by, source_message_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id, fact, category or "", source,
                    tags, created_at, expires_at or "",
                    supersedes or "", "", source_message_ids or "",
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

        return self.get(memory_id) or {}

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

    def _rebuild_fts_index(self, conn: sqlite3.Connection):
        """重建 memories_fts 索引：清空后从 memories 内容表重新索引。

        memories_fts 是 external content 表且存的是预分词文本，普通 DELETE
        会在连续删除时触发 'database disk image is malformed'（与 chunks_fts
        contentless 表同类问题，见 bm25.py）。统一用 delete-all + 重建，稳定且幂等。
        """
        conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('delete-all')")
        rows = conn.execute(
            f"SELECT rowid, fact, tags, category FROM {MEMORIES_TABLE}"
        ).fetchall()
        for rowid, fact, tags, category in rows:
            conn.execute(
                f"INSERT INTO {FTS_TABLE}(rowid, fact, tags, category) VALUES (?, ?, ?, ?)",
                (rowid, tokenize_text(fact), tokenize_text(tags or ""), category or ""),
            )

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
                   m.expires_at, m.supersedes, m.superseded_by, m.source_message_ids,
                   rank AS score
            FROM {FTS_TABLE} f
            JOIN {MEMORIES_TABLE} m ON m.rowid = f.rowid
            WHERE {FTS_TABLE} MATCH ?
        """
        params: list = [self._escape_fts_query(query)]

        if not params[0]:
            # 噪音查询分词后为空：短路返回，避免 MATCH '' 抛 FTS5 语法错误
            return []

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
                # FTS5 bm25 rank 越负越相关：score = -rank 使「分越高越相关」
                score = round(-float(raw_rank), 3)
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
                "source_message_ids": row["source_message_ids"] or "",
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

    def persona(
        self,
        per_category: int = 5,
        max_facts: int = 20,
        include_superseded: bool = False,
    ) -> dict:
        """轻量画像层：按 category 聚合有效记忆，零 LLM 依赖。

        对标 TencentDB 的 L3 Persona：把零散 fact 按类别聚合成
        高密度画像（偏好/项目/技术栈…），供 shibei_context 答前注入。

        Returns:
            {
                "categories": {category: [fact, ...]},
                "total": int,
                "updated_at": str,
            }
        """
        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            sql = f"""
                SELECT fact, category, created_at, source_message_ids FROM {MEMORIES_TABLE}
                WHERE 1=1
            """
            params: list = []
            if not include_superseded:
                sql += " AND (superseded_by IS NULL OR superseded_by = '')"
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql, params).fetchall()

        categories: dict[str, list[dict]] = {}
        total = 0
        for r in rows:
            cat = (r["category"] or "user").strip() or "user"
            if len(categories.get(cat, [])) >= per_category:
                continue
            if total >= max_facts:
                break
            categories.setdefault(cat, []).append({
                "fact": r["fact"],
                "created_at": r["created_at"],
                "source_message_ids": r["source_message_ids"] or "",
            })
            total += 1

        return {
            "categories": categories,
            "total": total,
            "updated_at": self._now(),
        }

    def forget(self, memory_id: str) -> bool:
        """删除一条记忆及其 FTS 索引。"""
        return self.delete(memory_id)

    def delete(self, memory_id: str) -> bool:
        """删除一条记忆及其 FTS 索引（实现 AbstractMemoryStore 接口）。

        支持 UUID 前缀匹配（CLI 打印的 id 前 8 位）；对 LIKE 通配符转义，
        避免用户输入中的 % / _ 误匹配多条。
        """
        rowid = self._rowid_by_id(memory_id)
        if rowid is None:
            with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
                escaped = _escape_like(memory_id)
                row = conn.execute(
                    f"SELECT rowid FROM {MEMORIES_TABLE} WHERE id LIKE ? ESCAPE '\\'",
                    (f"{escaped}%",),
                ).fetchone()
                if row:
                    rowid = row[0]
                else:
                    return False

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            cur = conn.execute(
                f"DELETE FROM {MEMORIES_TABLE} WHERE rowid = ?", (rowid,)
            )
            escaped = _escape_like(memory_id)
            conn.execute(
                f"UPDATE {MEMORIES_TABLE} SET superseded_by = '' WHERE superseded_by = ?",
                (memory_id,),
            )
            conn.execute(
                f"UPDATE {MEMORIES_TABLE} SET superseded_by = '' "
                f"WHERE superseded_by LIKE ? ESCAPE '\\'",
                (f"{escaped}%",),
            )
            self._rebuild_fts_index(conn)
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
        """删除已经过期的记忆，返回删除条数。

        按 datetime 语义比较（而非 ISO 字符串字典序），兼容不同时区偏移
        与省略秒/时区的写入格式；无法解析的 expires_at 视为不过期。
        先删内容表，再一次性重建 FTS 索引（避免连续 DELETE 损坏索引）。
        """
        if now is None:
            now = datetime.now(timezone.utc)

        with sqlite3.connect(str(self.db_file), timeout=30.0) as conn:
            rows = conn.execute(
                f"""
                SELECT rowid, expires_at FROM {MEMORIES_TABLE}
                WHERE expires_at IS NOT NULL AND expires_at != ''
                  AND (superseded_by IS NULL OR superseded_by = '')
                """
            ).fetchall()

            expired_rowids = []
            for rowid, expires_at in rows:
                exp = _parse_dt(expires_at)
                if exp is not None and exp <= now:
                    expired_rowids.append(rowid)

            if expired_rowids:
                placeholders = ",".join("?" * len(expired_rowids))
                conn.execute(
                    f"DELETE FROM {MEMORIES_TABLE} WHERE rowid IN ({placeholders})",
                    expired_rowids,
                )
                self._rebuild_fts_index(conn)
            conn.commit()
        return len(expired_rowids)

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
            conn.execute(
                f"""
                UPDATE {MEMORIES_TABLE}
                SET fact = ?, tags = ?
                WHERE rowid = ?
                """,
                (fact, new_tags, rowid),
            )
            # 内容变更后重建 FTS 索引（避免 FTS delete+insert 在 external content 表上的索引损坏）
            self._rebuild_fts_index(conn)
            conn.commit()
        return True
