"""BM25 搜索引擎 — SQLite FTS5 实现，零额外依赖。

中文分词策略：
  - 优先使用 jieba（若安装）：精确分词，搜索质量更高
  - 降级到 bigram + 单字：零依赖兜底

FTS5 存分词语料做索引，原文存独立表。
"""

import sqlite3
from pathlib import Path

from .tokenizer import tokenize_text, tokenize_query


DB_NAME = "fts5.db"
CONTENT_TABLE = "chunks"
FTS_TABLE = "chunks_fts"
SUMMARY_COL = "summary"


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

            conn.commit()

    def clear(self):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.execute(f"DELETE FROM {CONTENT_TABLE}")
            conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('delete-all')")
            conn.commit()

    def add_batch(self, items: list):
        """批量添加 chunks。

        兼容格式：
          4-tuple: (source, file_path, tags, content)
          6-tuple: (source, file_path, tags, content, parent_text, parent_id)
          7-tuple: (..., summary)
        """
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
                # FTS5 content='' mode: rowid must match
                conn.execute(
                    f"INSERT INTO {FTS_TABLE} (rowid, content_tokens, summary_tokens) VALUES (?, ?, ?)",
                    (rowid, content_tokens, summary_tokens),
                )
            conn.commit()

    def search(self, query: str, limit: int = 5, tag: 'str | None' = None) -> list[dict]:
        """BM25 搜索，同时检索正文和摘要，RRF 融合后返回原文。"""
        tokenized = tokenize_query(query)
        content_rows = self._search_field("content_tokens", tokenized, limit * 3, tag)
        summary_rows = self._search_field("summary_tokens", tokenized, limit * 3, tag)
        return self._rrf_fuse_rows(content_rows, summary_rows, limit)

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
                    WHERE {FTS_TABLE} MATCH ? AND c.tags LIKE ?
                    ORDER BY f.rank
                    LIMIT ?
                """
                rows = conn.execute(sql, (match_expr, f"%{tag}%", limit)).fetchall()
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
        rank = row[7] if len(row) > 7 else row[4]
        rank = float(rank) if rank else 0.0
        item = {
            "_rowid": row[0],
            "source": source,
            "file_path": file_path,
            "score": round(1.0 / (1.0 + abs(rank)) if rank else 1.0, 3),
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
        k: int = 60,
    ) -> list[dict]:
        """RRF 融合正文和摘要的搜索结果。"""
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
        """删除指定 source 的所有 chunks（按 rowid 精确删除）。

        FTS5 contentless 表不支持直接 DELETE，
        需用特殊 'delete' 命令并提供原始 tokens。
        由于 tokenization 确定性，可从 content/summary 重新生成 tokens。
        复杂度：O(k) per source，k = 该 source 的 chunk 数。
        """
        if not sources:
            return
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            for source in sources:
                rows = conn.execute(
                    f"SELECT rowid, content, {SUMMARY_COL} FROM {CONTENT_TABLE} WHERE source = ?",
                    (source,),
                ).fetchall()
                for rowid, content, summary in rows:
                    # FTS5 contentless: 用 'delete' 命令 + 原始 tokens 删除索引项
                    content_tokens = tokenize_text(content or "")
                    summary_tokens = tokenize_text(summary or "")
                    conn.execute(
                        f"INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, content_tokens, summary_tokens) "
                        f"VALUES('delete', ?, ?, ?)",
                        (rowid, content_tokens, summary_tokens),
                    )
                    conn.execute(
                        f"DELETE FROM {CONTENT_TABLE} WHERE rowid = ?",
                        (rowid,),
                    )
            conn.commit()

    def list_sources(self) -> list[str]:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT source FROM {CONTENT_TABLE} ORDER BY source"
            ).fetchall()
            return [r[0] for r in rows]

    def delete_by_rowids(self, rowids: list[int]):
        """按 rowid 精确删除 chunks 及其 FTS 索引。

        FTS5 contentless 表需用 'delete' 命令 + 原始 tokens 删除。
        """
        if not rowids:
            return

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            for rowid in rowids:
                row = conn.execute(
                    f"SELECT content, {SUMMARY_COL} FROM {CONTENT_TABLE} WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
                if not row:
                    continue
                content, summary = row
                content_tokens = tokenize_text(content or "")
                summary_tokens = tokenize_text(summary or "")
                conn.execute(
                    f"INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, content_tokens, summary_tokens) "
                    f"VALUES('delete', ?, ?, ?)",
                    (rowid, content_tokens, summary_tokens),
                )
                conn.execute(f"DELETE FROM {CONTENT_TABLE} WHERE rowid = ?", (rowid,))
            conn.commit()
