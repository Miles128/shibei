"""BM25 搜索引擎 — SQLite FTS5 实现，零额外依赖。

中文分词策略：
  - 优先使用 jieba（若安装）：精确分词，搜索质量更高
  - 降级到 bigram + 单字：零依赖兜底

FTS5 存分词语料做索引，原文存独立表。
"""

import logging
import re
import sqlite3
import struct
from pathlib import Path
from typing import TYPE_CHECKING

from .tokenizer import query_coverage_groups, tokenize_query, tokenize_text

if TYPE_CHECKING:
    from .config import VectorConfig

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^#{1,6}\s+(.+)$")


def extract_section(content: str) -> str:
    """从 chunk 首个标题行提取 section；没有标题返回空串。"""
    for line in (content or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = _SECTION_RE.match(line)
        return match.group(1).strip() if match else ""
    return ""



DB_NAME = "fts5.db"
CONTENT_TABLE = "chunks"
FTS_TABLE = "chunks_fts"
SUMMARY_COL = "summary"
EMB_TABLE = "chunk_embeddings"

# 查询核心词覆盖率门槛：低于此值的结果视为「只命中泛词」，直接丢弃。
# 本地真实评测集（32 条）上，0.55 把负例误召从 4/4 降到 0/4；
# hybrid 相对纯 BM25 主要提升排序质量（MRR 0.699→0.744），R@5 持平。
MIN_QUERY_COVERAGE = 0.55


class BM25Index:
    """SQLite FTS5 BM25 索引。

    架构：chunks 表存原文，chunks_fts 存分词索引（content='' 纯索引模式）。
    """

    def __init__(self, db_path: str, vector_config: "VectorConfig | None" = None):
        self.db_path = db_path
        self.vector_config = vector_config
        self._hnsw = None
        self._hnsw_unavailable = False
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
        self._invalidate_hnsw()

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

    def _get_hnsw(self):
        """返回共享 HNSW 索引；未启用或依赖缺失时返回 None。"""
        cfg = self.vector_config
        if (
            cfg is None
            or not cfg.enabled
            or cfg.backend != "hnsw"
            or not cfg.add_new_rows
        ):
            return None
        if self._hnsw_unavailable:
            return None
        if self._hnsw is None:
            from .vector_index import get_shared_hnsw_index, usearch_available

            if not usearch_available():
                logger.warning("vector.backend=hnsw 但 usearch/numpy 不可用，回退精确向量检索")
                self._hnsw_unavailable = True
                return None
            self._hnsw = get_shared_hnsw_index(self.db_path, cfg)
        return self._hnsw

    def _remove_from_hnsw(self, rowids: list[int]) -> None:
        if not rowids:
            return
        index = self._get_hnsw()
        if index is not None:
            index.remove(rowids)
        else:
            from .vector_index import invalidate_vector_index

            invalidate_vector_index(self.db_path)

    def _invalidate_hnsw(self) -> None:
        from .vector_index import invalidate_vector_index

        invalidate_vector_index(self.db_path)

    def _filter_by_tag(
        self,
        hits: list[tuple[int, float]],
        tag: str,
    ) -> list[tuple[int, float]]:
        if not hits:
            return []
        rowids = [rowid for rowid, _ in hits]
        placeholders = ",".join("?" * len(rowids))
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            rows = conn.execute(
                f"SELECT rowid FROM {CONTENT_TABLE} "
                f"WHERE rowid IN ({placeholders}) "
                f"AND (',' || tags || ',') LIKE ?",
                [*rowids, f"%,{tag},%"],
            ).fetchall()
        allowed = {row[0] for row in rows}
        return [(rowid, score) for rowid, score in hits if rowid in allowed]

    def _exact_search(
        self,
        query_vec: list[float],
        limit: int,
        tag: 'str | None' = None,
        min_score: float = 0.0,
        candidate_rowids: 'set[int] | None' = None,
    ) -> list[tuple[int, float]]:
        """精确余弦检索；candidate_rowids 用于只重排 BM25 候选。"""
        if not query_vec:
            return []
        if candidate_rowids is not None and not candidate_rowids:
            return []

        sql = f"SELECT e.rowid, e.vector FROM {EMB_TABLE} e"
        where: list[str] = []
        params: list = []
        if candidate_rowids is not None:
            placeholders = ",".join("?" * len(candidate_rowids))
            where.append(f"e.rowid IN ({placeholders})")
            params.extend(sorted(candidate_rowids))
        if tag:
            sql += f" JOIN {CONTENT_TABLE} c ON c.rowid = e.rowid"
            where.append("(',' || c.tags || ',') LIKE ?")
            params.append(f"%,{tag},%")
        if where:
            sql += " WHERE " + " AND ".join(where)

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            rows = conn.execute(sql, params).fetchall()

        scored = [
            (rowid, _cosine(_unpack_vector(blob), query_vec))
            for rowid, blob in rows
        ]
        scored = [item for item in scored if item[1] >= min_score]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

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
        index = self._get_hnsw()
        if index is not None:
            index.add(items)

    def clear_embeddings(self):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.execute(f"DELETE FROM {EMB_TABLE}")
            conn.commit()
        self._invalidate_hnsw()

    def search_embeddings(
        self,
        query_vec: list[float],
        limit: int = 10,
        tag: 'str | None' = None,
        min_score: float = 0.0,
        candidate_rowids: 'set[int] | None' = None,
    ) -> list[tuple[int, float]]:
        """向量 top-k 检索。

        - candidate_rowids 非空：只对这批 rowid 做精确余弦（add_new_rows=false 的重排路径）
        - backend=hnsw：走 usearch，tag 过滤时多取候选，不足则精确兜底
        - 其他情况：BLOB 全量加载 + 纯 Python 余弦

        Returns:
            [(rowid, cosine), ...] 按相似度降序
        """
        if not query_vec:
            return []
        if candidate_rowids is not None:
            return self._exact_search(
                query_vec, limit, tag, min_score, candidate_rowids
            )

        index = self._get_hnsw()
        if index is not None:
            cfg = self.vector_config
            overfetch = cfg.hnsw.overfetch if tag and cfg is not None else 1
            try:
                hits = index.search(
                    query_vec,
                    max(limit * overfetch, limit),
                    min_score=min_score,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("HNSW 检索异常，回退精确检索: %s", exc)
                hits = None
            if hits is not None:
                if tag:
                    hits = self._filter_by_tag(hits, tag)
                    if len(hits) < limit:
                        # 过滤后候选不足：对 tagged 子集做精确兜底
                        return self._exact_search(query_vec, limit, tag, min_score)
                return hits[:limit]

        return self._exact_search(query_vec, limit, tag, min_score)

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
        coverage_groups: 'list[list[str]] | None' = None,
        stats: 'dict | None' = None,
        with_why: bool = False,
    ) -> list[dict]:
        """BM25 搜索，同时检索正文和摘要，RRF 融合后返回原文。

        Args:
            query: 原始查询字符串（仅用于日志/调试）。
            limit: 返回结果数。
            tag: 标签过滤。
            tokenized_query: 若提供则直接使用该 tokenized 表达式，跳过内部 tokenize_query。
            coverage_groups: 相关性门槛用的查询词组（组内同义词任一命中即可）；None 表示关闭覆盖率过滤。
            stats: 回填候选数等诊断信息。
            with_why: 为每条结果附命中路由与排名。
        """
        tokenized = tokenized_query if tokenized_query is not None else tokenize_query(query)
        # 噪音查询（如 "###"）分词后为空：直接短路，避免 MATCH '' 抛 FTS5 语法错误
        if not tokenized.strip():
            return []
        content_rows = self._search_field("content_tokens", tokenized, limit * 3, tag)
        summary_rows = self._search_field("summary_tokens", tokenized, limit * 3, tag)
        return self._rrf_fuse_rows(
            query, content_rows, summary_rows, limit,
            coverage_groups=coverage_groups,
            stats=stats,
            with_why=with_why,
        )

    def search_hybrid(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        tokenized_query: 'str | None' = None,
        query_vec: 'list[float] | None' = None,
        vec_k: int = 60,
        min_similarity: float = 0.0,
        add_new_rows: bool = True,
        coverage_groups: 'list[list[str]] | None' = None,
        stats: 'dict | None' = None,
        with_why: bool = False,
    ) -> list[dict]:
        """混合检索：BM25 正文 + BM25 摘要 + 向量 三路 RRF 融合。

        query_vec 为 None 或向量检索无结果时退化为纯 BM25 双路。
        add_new_rows=False 时向量只重排 BM25 已命中的行，不引入新文档。
        """
        tokenized = tokenized_query if tokenized_query is not None else tokenize_query(query)
        if not tokenized.strip():
            return []
        content_rows = self._search_field("content_tokens", tokenized, limit * 3, tag)
        summary_rows = self._search_field("summary_tokens", tokenized, limit * 3, tag)

        if not query_vec:
            return self._rrf_fuse_rows(
                query, content_rows, summary_rows, limit,
                coverage_groups=coverage_groups,
                stats=stats,
                with_why=with_why,
            )

        candidate_rowids = None
        if not add_new_rows:
            candidate_rowids = {row[0] for row in content_rows}
            candidate_rowids.update(row[0] for row in summary_rows)

        vec_hits = self.search_embeddings(
            query_vec,
            limit * 3,
            tag,
            min_score=min_similarity,
            candidate_rowids=candidate_rowids,
        )
        return self._rrf_fuse_rows(
            query,
            content_rows,
            summary_rows,
            limit,
            vec_hits=vec_hits,
            k=vec_k,
            add_new_rows=add_new_rows,
            coverage_groups=coverage_groups,
            stats=stats,
            with_why=with_why,
        )

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
                           c.parent_text, c.parent_id, f.rank, c.summary
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
                           c.parent_text, c.parent_id, f.rank, c.summary
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
            "section": extract_section(content),
        }
        if parent_text:
            item["parent_text"] = parent_text[:800]
            item["parent_id"] = parent_id
        return item

    def fetch_rows(self, rowids: 'list[int]') -> dict[int, dict]:
        """按 rowid 取完整正文与 parent 上下文（默认结果里的 text 是截断的）。"""
        ids = [int(r) for r in rowids if r is not None]
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            rows = conn.execute(
                f"SELECT rowid, source, file_path, tags, content, parent_text "
                f"FROM {CONTENT_TABLE} WHERE rowid IN ({placeholders})",
                ids,
            ).fetchall()
        out: dict[int, dict] = {}
        for row in rows:
            out[int(row[0])] = {
                "source": row[1],
                "file_path": row[2],
                "tags": row[3],
                "text": row[4] or "",
                "parent_text": row[5] or "",
                "section": extract_section(row[4] or ""),
            }
        return out

    def _rrf_fuse_rows(
        self,
        query: str,
        content_rows: list[tuple],
        summary_rows: list[tuple],
        limit: int,
        vec_hits: 'list[tuple[int, float]] | None' = None,
        k: int = 60,
        add_new_rows: bool = True,
        coverage_groups: 'list[list[str]] | None' = None,
        stats: 'dict | None' = None,
        with_why: bool = False,
    ) -> list[dict]:
        """RRF 融合正文、摘要、（可选）向量的搜索结果。

        add_new_rows=False 时只给 BM25 已命中的行加向量加权，避免纯向量噪声
        在 BM25 零命中时冒充结果；融合后再按查询核心词覆盖率过滤泛词命中。
        coverage_groups=None 时关闭覆盖率过滤（同义词/模糊扩展场景）。

        stats: 传入 dict 时回填候选数与覆盖率过滤丢弃数（供 UI/诊断用）。
        with_why: 为每条结果附 ``why``（命中路由与各路由排名），供 UI 展示。
        """
        rrf_scores: dict[int, float] = {}
        best_item: dict[int, dict] = {}
        summaries: dict[int, str] = {}
        bm25_rowids: set[int] = set()
        route_ranks: dict[int, dict[str, int]] = {}

        for rank, row in enumerate(content_rows, start=1):
            rowid = row[0]
            rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (k + rank)
            bm25_rowids.add(rowid)
            summaries[rowid] = row[8] if len(row) > 8 else ""
            route_ranks.setdefault(rowid, {})["content"] = rank
            if rowid not in best_item:
                best_item[rowid] = self._row_to_item(row)

        for rank, row in enumerate(summary_rows, start=1):
            rowid = row[0]
            rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (k + rank)
            bm25_rowids.add(rowid)
            summaries[rowid] = row[8] if len(row) > 8 else ""
            route_ranks.setdefault(rowid, {})["summary"] = rank
            if rowid not in best_item:
                best_item[rowid] = self._row_to_item(row)

        vec_sims: dict[int, float] = {}
        if vec_hits:
            # 向量路按余弦相似度降序排名
            vec_sims = {rowid: sim for rowid, sim in vec_hits}
            if add_new_rows:
                # 仅向量命中、BM25 未命中的行：从内容表补全原文，避免返回空 text
                missing = [rowid for rowid in vec_sims if rowid not in best_item]
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
            for rank, rowid in enumerate(vec_sims, start=1):
                if rowid not in best_item:
                    continue
                route_ranks.setdefault(rowid, {})["vector"] = rank
                rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (k + rank)

        fused = sorted(
            best_item.values(),
            key=lambda x: rrf_scores[x.get("_rowid", 0)],
            reverse=True,
        )

        if coverage_groups:
            fused = [
                item for item in fused
                if item.get("_rowid") not in bm25_rowids
                or query_coverage_groups(
                    "\n".join([
                        item.get("text", ""),
                        item.get("parent_text", ""),
                        summaries.get(item.get("_rowid", 0), ""),
                    ]),
                    coverage_groups,
                ) >= MIN_QUERY_COVERAGE
            ]
        coverage_dropped = max(len(best_item) - len(fused), 0)
        fused = fused[:limit]

        # 给结果加上 rank，并保住 rowid 在内部字段方便调试
        results = []
        for i, item in enumerate(fused, start=1):
            item = dict(item)
            item["rank"] = i
            sim = vec_sims.get(item.get("_rowid", 0))
            if sim is not None:
                item["vector_score"] = round(sim, 4)
                if not item.get("score"):
                    # 纯向量命中：用相似度代替无意义的 0 分
                    item["score"] = round(sim, 3)
                    item["score_kind"] = "vector"
                else:
                    item["score_kind"] = "bm25"
            else:
                item["score_kind"] = "bm25"
            if with_why:
                ranks = route_ranks.get(item.get("_rowid", 0), {})
                item["why"] = {
                    "routes": sorted(ranks, key=lambda r: ranks[r]),
                    "ranks": ranks,
                }
            results.append(item)

        if stats is not None:
            stats.update({
                "candidates": {
                    "content": len(content_rows),
                    "summary": len(summary_rows),
                    "vector": len(vec_sims),
                },
                "fused": len(best_item),
                "coverage_dropped": coverage_dropped,
                "returned": len(results),
            })
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

    def count_chunks(
        self,
        source: 'str | None' = None,
        tag: 'str | None' = None,
    ) -> int:
        """按 source/tag 过滤后的 chunk 数。"""
        clause, params = self._chunk_filter(source, tag)
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {CONTENT_TABLE} {clause}", params
            ).fetchone()
        return int(row[0]) if row else 0

    def list_chunks(
        self,
        limit: int = 200,
        offset: int = 0,
        source: 'str | None' = None,
        tag: 'str | None' = None,
    ) -> list[dict]:
        """按 rowid 顺序分页列出 chunk 原文（用于「全部片段」浏览）。"""
        clause, params = self._chunk_filter(source, tag)
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            rows = conn.execute(
                f"SELECT rowid, source, file_path, tags, content, parent_text "
                f"FROM {CONTENT_TABLE} {clause} ORDER BY rowid LIMIT ? OFFSET ?",
                [*params, max(int(limit), 0), max(int(offset), 0)],
            ).fetchall()
        return [
            {
                "_rowid": row[0],
                "source": row[1],
                "file_path": row[2],
                "tags": row[3] or "",
                "text": row[4] or "",
                "parent_text": row[5] or "",
                "section": extract_section(row[4] or ""),
            }
            for row in rows
        ]

    @staticmethod
    def _chunk_filter(
        source: 'str | None',
        tag: 'str | None',
    ) -> tuple[str, list]:
        where: list[str] = []
        params: list = []
        if source:
            where.append("source = ?")
            params.append(source)
        if tag:
            where.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{tag},%")
        return (f"WHERE {' AND '.join(where)}" if where else "", params)

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
            rows = conn.execute(
                f"SELECT rowid FROM {CONTENT_TABLE} WHERE source IN ({placeholders})",
                sources,
            ).fetchall()
            rowids = [row[0] for row in rows]
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
        self._remove_from_hnsw(rowids)

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
        self._remove_from_hnsw(rowids)

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
        # 新 chunk 暂无 embedding，索引按数据库重建前先失效
        self._invalidate_hnsw()


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
