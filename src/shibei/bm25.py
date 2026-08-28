"""BM25 搜索引擎 — SQLite FTS5 实现，零额外依赖。

中文分词策略：
  - 优先使用 jieba（若安装）：精确分词，搜索质量更高
  - 降级到 bigram + 单字：零依赖兜底

FTS5 存分词语料做索引，原文存独立表。
"""

import sqlite3
import struct
from pathlib import Path

from .tokenizer import tokenize_text, tokenize_query


DB_NAME = "fts5.db"
CONTENT_TABLE = "chunks"
FTS_TABLE = "chunks_fts"
SUMMARY_COL = "summary"
EMB_TABLE = "chunk_embeddings"


class BM25Index:
    """SQLite FTS5 BM25 索引。

    架构：chunks 表存原文，chunks_fts 存分词索引（content='' 纯索引模式）。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            # chunks 表：兼容旧 schema（无 parent/summary 列时自动 ALTER 添加）
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {CONTENT_TABLE} (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    file_path TEXT,
                    tags TEXT,
                    content TEXT
                )
            """)
            # 自动迁移：添加 parent_text, parent_id, summary 列（若不存在）
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({CONTENT_TABLE})").fetchall()}
            if "parent_text" not in cols:
                conn.execute(f"ALTER TABLE {CONTENT_TABLE} ADD COLUMN parent_text TEXT DEFAULT ''")
            if "parent_id" not in cols:
                conn.execute(f"ALTER TABLE {CONTENT_TABLE} ADD COLUMN parent_id TEXT DEFAULT ''")
            if SUMMARY_COL not in cols:
                conn.execute(f"ALTER TABLE {CONTENT_TABLE} ADD COLUMN {SUMMARY_COL} TEXT DEFAULT ''")

            # FTS5 列结构：content_tokens + summary_tokens
            # 如果旧表是单列 tokens，则删除重建
            fts_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({FTS_TABLE})").fetchall()}
            need_rebuild = False
            if fts_cols and fts_cols != {"content_tokens", "summary_tokens"}:
                need_rebuild = True
                conn.execute(f"DROP TABLE {FTS_TABLE}")

            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
                    content_tokens,
                    summary_tokens,
                    content=''
                )
            """)

            if need_rebuild:
                # 从 content 表重新索引所有内容
                rows = conn.execute(
                    f"SELECT rowid, content, {SUMMARY_COL} FROM {CONTENT_TABLE}"
                ).fetchall()
                for rowid, content, summary in rows:
                    conn.execute(
                        f"INSERT INTO {FTS_TABLE} (rowid, content_tokens, summary_tokens) VALUES (?, ?, ?)",
                        (rowid, tokenize_text(content or ""), tokenize_text(summary or "")),
                    )

            # 向量表：BLOB 存稠密向量（PRD 10.3 规格：chunk_embeddings(rowid, dim, vector)）
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {EMB_TABLE} (
                    rowid INTEGER PRIMARY KEY,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL
                )
            """)

            conn.commit()

    def _rebuild_fts_index(self, conn: sqlite3.Connection):
        """重建 FTS5 索引：清空后从 content 表重新索引所有内容。

        用于批量删除后修复 contentless FTS5 索引，避免 'database disk image is malformed'。
        """
        conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('delete-all')")
        rows = conn.execute(
            f"SELECT rowid, content, {SUMMARY_COL} FROM {CONTENT_TABLE}"
        ).fetchall()
        for rowid, content, summary in rows:
            conn.execute(
                f"INSERT INTO {FTS_TABLE} (rowid, content_tokens, summary_tokens) VALUES (?, ?, ?)",
                (rowid, tokenize_text(content or ""), tokenize_text(summary or "")),
            )

    def clear(self):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.execute(f"DELETE FROM {CONTENT_TABLE}")
            conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('delete-all')")
            conn.execute(f"DELETE FROM {EMB_TABLE}")
            conn.commit()

    def add_batch(self, items: list) -> list[int]:
        """批量添加 chunks，返回新插入的 rowid 列表（供向量索引联动）。

        兼容格式：
          4-tuple: (source, file_path, tags, content)
          6-tuple: (source, file_path, tags, content, parent_text, parent_id)
          7-tuple: (..., summary)
        """
        new_rowids: list[int] = []
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            for item in items:
                source, file_path, tags, content = item[0], item[1], item[2], item[3]
                parent_text = item[4] if len(item) > 4 else ""
                parent_id = item[5] if len(item) > 5 else ""
                summary = item[6] if len(item) > 6 else ""
                content_tokens = tokenize_text(content)
                summary_tokens = tokenize_text(summary)
                cur = conn.execute(
                    f"INSERT INTO {CONTENT_TABLE} "
                    f"(source, file_path, tags, content, parent_text, parent_id, {SUMMARY_COL}) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source, file_path, tags, content, parent_text, parent_id, summary),
                )
                rowid = cur.lastrowid
                if rowid is None:
                    raise sqlite3.IntegrityError("INSERT 未返回 rowid")
                # FTS5 content='' mode: rowid must match
                conn.execute(
                    f"INSERT INTO {FTS_TABLE} (rowid, content_tokens, summary_tokens) VALUES (?, ?, ?)",
                    (rowid, content_tokens, summary_tokens),
                )
                new_rowids.append(rowid)
            conn.commit()
        return new_rowids

    # ---------- 向量混合检索（可选，PRD 10.3） ----------

    def store_embeddings(self, items: list[tuple[int, list[float]]]):
        """批量写入 chunk 向量（rowid, vector）。重复 rowid 覆盖。"""
        if not items:
            return
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            for rowid, vector in items:
                conn.execute(
                    f"INSERT OR REPLACE INTO {EMB_TABLE} (rowid, dim, vector) VALUES (?, ?, ?)",
                    (rowid, len(vector), _pack_vector(vector)),
                )
            conn.commit()

    def clear_embeddings(self):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.execute(f"DELETE FROM {EMB_TABLE}")
            conn.commit()

    def search_embeddings(
        self,
        query_vec: list[float],
        limit: int = 10,
        tag: 'str | None' = None,
    ) -> list[tuple[int, float]]:
        """向量 top-k 检索：BLOB 全量加载 + 纯 Python 余弦（零依赖，小语料够用）。

        Returns:
            [(rowid, cosine), ...] 按相似度降序
        """
        if not query_vec:
            return []
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            if tag:
                rows = conn.execute(
                    f"""
                    SELECT e.rowid, e.vector FROM {EMB_TABLE} e
                    JOIN {CONTENT_TABLE} c ON c.rowid = e.rowid
                    WHERE (',' || c.tags || ',') LIKE ?
                    """,
                    (f"%,{tag},%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT rowid, vector FROM {EMB_TABLE}"
                ).fetchall()
        scored = [
            (rowid, _cosine(_unpack_vector(blob), query_vec))
            for rowid, blob in rows
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def count_embeddings(self) -> int:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {EMB_TABLE}").fetchone()
            return row[0] if row else 0

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        tokenized_query: 'str | None' = None,
    ) -> list[dict]:
        """BM25 搜索，同时检索正文和摘要，RRF 融合后返回原文。

        Args:
            query: 原始查询字符串（仅用于日志/调试）。
            limit: 返回结果数。
            tag: 标签过滤。
            tokenized_query: 若提供则直接使用该 tokenized 表达式，跳过内部 tokenize_query。
        """
        tokenized = tokenized_query if tokenized_query is not None else tokenize_query(query)
        # 噪音查询（如 "###"）分词后为空：直接短路，避免 MATCH '' 抛 FTS5 语法错误
        if not tokenized.strip():
            return []
        content_rows = self._search_field("content_tokens", tokenized, limit * 3, tag)
        summary_rows = self._search_field("summary_tokens", tokenized, limit * 3, tag)
        return self._rrf_fuse_rows(content_rows, summary_rows, limit)

    def search_hybrid(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        tokenized_query: 'str | None' = None,
        query_vec: 'list[float] | None' = None,
        vec_k: int = 60,
    ) -> list[dict]:
        """混合检索：BM25 正文 + BM25 摘要 + 向量 三路 RRF 融合。

        query_vec 为 None 或向量检索无结果时退化为纯 BM25 双路。
        """
        tokenized = tokenized_query if tokenized_query is not None else tokenize_query(query)
        if not tokenized.strip():
            return []
        content_rows = self._search_field("content_tokens", tokenized, limit * 3, tag)
        summary_rows = self._search_field("summary_tokens", tokenized, limit * 3, tag)

        if not query_vec:
            return self._rrf_fuse_rows(content_rows, summary_rows, limit)

        vec_hits = self.search_embeddings(query_vec, limit * 3, tag)
        return self._rrf_fuse_rows(content_rows, summary_rows, limit, vec_hits=vec_hits, k=vec_k)

    def _search_field(
        self,
        field: str,
        tokenized: str,
        limit: int,
        tag: 'str | None' = None,
    ) -> list[tuple]:
        """在指定 FTS 列上搜索。"""
        match_expr = f"{field}: {tokenized}" if field else tokenized
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            if tag:
                sql = f"""
                    SELECT c.rowid, c.source, c.file_path, c.tags, c.content,
                           c.parent_text, c.parent_id, f.rank
                    FROM {FTS_TABLE} f
                    JOIN {CONTENT_TABLE} c ON f.rowid = c.rowid
                    WHERE {FTS_TABLE} MATCH ? AND (',' || c.tags || ',') LIKE ?
                    ORDER BY f.rank
                    LIMIT ?
                """
                # 标签精确边界匹配：",ai," 命中 ",ai," 但不命中 ",daily,"
                rows = conn.execute(sql, (match_expr, f"%,{tag},%", limit)).fetchall()
            else:
                sql = f"""
                    SELECT c.rowid, c.source, c.file_path, c.tags, c.content,
                           c.parent_text, c.parent_id, f.rank
                    FROM {FTS_TABLE} f
                    JOIN {CONTENT_TABLE} c ON f.rowid = c.rowid
                    WHERE {FTS_TABLE} MATCH ?
                    ORDER BY f.rank
                    LIMIT ?
                """
                rows = conn.execute(sql, (match_expr, limit)).fetchall()
        return rows

    @staticmethod
    def _row_to_item(row: tuple) -> dict:
        """把数据库行转成结果字典；row 顺序：rowid, source, file_path, tags, content, parent_text, parent_id, rank。"""
        _, source, file_path, tags, content = row[0], row[1], row[2], row[3], row[4]
        parent_text = row[5] if len(row) > 5 else ""
        parent_id = row[6] if len(row) > 6 else ""
        rank = row[7] if len(row) > 7 else 0.0
        rank = float(rank) if rank is not None else 0.0
        item = {
            "_rowid": row[0],
            "source": source,
            "file_path": file_path,
            # FTS5 bm25 rank 越负越相关：score = -rank 使「分越高越相关」
            "score": round(-rank, 3) if rank else 0.0,
            "tags": tags,
            "text": content[:300],
        }
        if parent_text:
            item["parent_text"] = parent_text[:800]
            item["parent_id"] = parent_id
        return item

    def _rrf_fuse_rows(
        self,
        content_rows: list[tuple],
        summary_rows: list[tuple],
        limit: int,
        vec_hits: 'list[tuple[int, float]] | None' = None,
        k: int = 60,
    ) -> list[dict]:
        """RRF 融合正文、摘要、（可选）向量的搜索结果。"""
        rrf_scores: dict[int, float] = {}
        best_item: dict[int, dict] = {}

        for rank, row in enumerate(content_rows, start=1):
            rowid = row[0]
            rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (k + rank)
            if rowid not in best_item:
                best_item[rowid] = self._row_to_item(row)

        for rank, row in enumerate(summary_rows, start=1):
            rowid = row[0]
            rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (k + rank)
            if rowid not in best_item:
                best_item[rowid] = self._row_to_item(row)

        if vec_hits:
            # 向量路按余弦相似度降序排名
            for rank, (rowid, _sim) in enumerate(vec_hits, start=1):
                rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (k + rank)
            # 仅向量命中、BM25 未命中的行：从内容表补全原文，避免返回空 text
            missing = [rowid for rowid, _ in vec_hits if rowid not in best_item]
            if missing:
                placeholders = ",".join("?" * len(missing))
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    rows = conn.execute(
                        f"SELECT rowid, source, file_path, tags, content, "
                        f"parent_text, parent_id, 0.0 AS rank "
                        f"FROM {CONTENT_TABLE} WHERE rowid IN ({placeholders})",
                        missing,
                    ).fetchall()
                for row in rows:
                    best_item[row[0]] = self._row_to_item(row)

        fused = sorted(
            best_item.values(),
            key=lambda x: rrf_scores[x.get("_rowid", 0)],
            reverse=True,
        )[:limit]

        # 给结果加上 rank，并保留 rowid 在内部字段方便调试
        results = []
        for i, item in enumerate(fused, start=1):
            item = dict(item)
            item["rank"] = i
            results.append(item)
        return results

    def count_sources(self) -> int:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            row = conn.execute(
                f"SELECT COUNT(DISTINCT source) FROM {CONTENT_TABLE}"
            ).fetchone()
            return row[0] if row else 0

    def count(self) -> int:
        """返回总 chunk 数。"""
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {CONTENT_TABLE}").fetchone()
            return row[0] if row else 0

    def list_sources_with_meta(self) -> list[dict]:
        """返回 source + tags 列表（按 source 聚合 tags）。"""
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            rows = conn.execute(
                f"SELECT source, GROUP_CONCAT(DISTINCT tags) AS tags "
                f"FROM {CONTENT_TABLE} GROUP BY source"
            ).fetchall()
        return [
            {"source": source, "tags": tags or ""}
            for source, tags in rows
        ]

    def remove_sources(self, sources: list[str]):
        """删除指定 source 的所有 chunks 及其 FTS 索引。

        先删除 content 表中的行，再重建整个 FTS5 索引。contentless FTS5 在大批量
        'delete' 操作下容易出现 'database disk image is malformed'，重建更稳定。
        """
        if not sources:
            return
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            placeholders = ",".join("?" * len(sources))
            conn.execute(
                f"DELETE FROM {CONTENT_TABLE} WHERE source IN ({placeholders})",
                sources,
            )
            # 联动清理向量
            conn.execute(
                f"DELETE FROM {EMB_TABLE} WHERE rowid NOT IN "
                f"(SELECT rowid FROM {CONTENT_TABLE})"
            )
            self._rebuild_fts_index(conn)
            conn.commit()

    def list_sources(self) -> list[str]:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT source FROM {CONTENT_TABLE} ORDER BY source"
            ).fetchall()
            return [r[0] for r in rows]

    def delete_by_rowids(self, rowids: list[int]):
        """按 rowid 精确删除 chunks 及其 FTS 索引。

        先删除 content 表中的行，再重建 FTS5 索引，避免 contentless FTS5
        在大批量 'delete' 操作下出现 'database disk image is malformed'。
        """
        if not rowids:
            return

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            placeholders = ",".join("?" * len(rowids))
            conn.execute(
                f"DELETE FROM {CONTENT_TABLE} WHERE rowid IN ({placeholders})",
                rowids,
            )
            conn.execute(
                f"DELETE FROM {EMB_TABLE} WHERE rowid IN ({placeholders})",
                rowids,
            )
            self._rebuild_fts_index(conn)
            conn.commit()

    def existing_rowids(self, rowids: list[int]) -> set[int]:
        """返回 rowids 中确实存在于 content 表的子集（用于外部输入校验）。"""
        if not rowids:
            return set()
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            placeholders = ",".join("?" * len(rowids))
            rows = conn.execute(
                f"SELECT rowid FROM {CONTENT_TABLE} WHERE rowid IN ({placeholders})",
                rowids,
            ).fetchall()
        return {r[0] for r in rows}

    def replace_by_rowids(self, rowids: list[int], items: list):
        """事务内原子完成「删除指定 rowids 的 chunks + 插入新 items」并重建 FTS 索引。

        供 consolidate / apply_consolidation 使用：避免分两次连接提交，
        中途异常导致旧 chunk 已删、新 chunk 未写入的中间状态。
        """
        if not rowids and not items:
            return
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            if rowids:
                placeholders = ",".join("?" * len(rowids))
                conn.execute(
                    f"DELETE FROM {CONTENT_TABLE} WHERE rowid IN ({placeholders})",
                    rowids,
                )
                conn.execute(
                    f"DELETE FROM {EMB_TABLE} WHERE rowid IN ({placeholders})",
                    rowids,
                )
            for item in items:
                source, file_path, tags, content = item[0], item[1], item[2], item[3]
                parent_text = item[4] if len(item) > 4 else ""
                parent_id = item[5] if len(item) > 5 else ""
                summary = item[6] if len(item) > 6 else ""
                conn.execute(
                    f"INSERT INTO {CONTENT_TABLE} "
                    f"(source, file_path, tags, content, parent_text, parent_id, {SUMMARY_COL}) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source, file_path, tags, content, parent_text, parent_id, summary),
                )
            self._rebuild_fts_index(conn)
            conn.commit()


# ---------- 向量编解码（零依赖，float32 LE 紧凑存储） ----------


def _pack_vector(vector: list[float]) -> bytes:
    """float32 little-endian 紧凑打包（PRD 10.3 vector BLOB）。"""
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（与 embedding.cosine_similarity 同实现，避免跨模块依赖）。"""
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
