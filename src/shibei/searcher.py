"""语义搜索引擎 — 仅 BM25 引擎

策略模式保留，但当前只实现 BM25Engine，未来如需扩展可继续注册。
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from .config import Config
from .bm25 import BM25Index
from .llm import LLMClient

logger = logging.getLogger(__name__)


class SearchEngine(ABC):
    """搜索引擎策略接口。"""

    @abstractmethod
    def search(self, query: str, limit: int, tag: 'str | None' = None) -> dict:
        """执行搜索，返回 {"query": ..., "total": ..., "results": [...]}。"""
        ...


class BM25Engine(SearchEngine):
    """BM25 搜索引擎（SQLite FTS5）"""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def search(self, query: str, limit: int, tag: 'str | None' = None) -> dict:
        bm25 = BM25Index(str(self.cfg.db_path_expanded / "fts5.db"))
        results = bm25.search(query, limit, tag)
        return {"query": query, "total": len(results), "results": results}


_ENGINES: dict[str, type[SearchEngine]] = {
    "bm25": BM25Engine,
}


def register_engine(name: str, engine_cls: type[SearchEngine]) -> None:
    """注册自定义搜索引擎。"""
    _ENGINES[name] = engine_cls
    logger.info("注册搜索引擎: %s", name)


class Searcher:
    """BM25 搜索入口，支持 LLM 查询改写和记忆召回。"""

    def __init__(self, cfg: Config, llm_client: Optional[LLMClient] = None):
        self.cfg = cfg
        self.llm = llm_client

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        include_memories: bool = True,
    ) -> dict:
        original_query = query

        # LLM 查询改写：失败时静默回退到原查询
        search_query = query
        if self.llm and self.llm.ready:
            try:
                rewritten = self.llm.rewrite_query(query)
                if rewritten:
                    search_query = rewritten
            except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
                logger.warning("LLM 查询改写失败，回退到原查询: %s", e)

        engine_name = self.cfg.storage.search_engine
        engine_cls = _ENGINES.get(engine_name)
        if engine_cls is None:
            logger.warning("Unknown search_engine '%s', fallback to bm25", engine_name)
            engine_cls = BM25Engine

        engine = engine_cls(self.cfg)
        result = engine.search(search_query, limit, tag)

        result["original_query"] = original_query
        result["rewritten_query"] = search_query

        if include_memories:
            result["memories"] = self._search_memories(original_query, limit)

        result["related"] = self._find_related(result.get("results", []))
        return result

    def _search_memories(self, query, limit):
        from .memory import MemoryStore

        store = MemoryStore(self.cfg.db_path_expanded)
        rows = store.search(query, limit=limit)
        return [
            {
                "type": "memory",
                "id": r["id"],
                "source": "memory",
                "score": round(float(r.get("score", 0.0)), 3),
                "text": r["fact"],
                "tags": r.get("tags", ""),
                "category": r.get("category", ""),
                "created_at": r.get("created_at", ""),
            }
            for r in rows
        ]

    def _find_related(self, results: list[dict], top_k: int = 3, limit: int = 3) -> list[dict]:
        """为前 top_k 个结果查找相关文档。"""
        if not results:
            return []

        from .related import RelatedFinder

        finder = RelatedFinder(self.cfg)
        seen: set[str] = set()
        related_all: list[dict] = []
        for r in results[:top_k]:
            source = r.get("source", "")
            if not source or source in seen:
                continue
            seen.add(source)
            try:
                for rel in finder.find_related(source, limit=limit):
                    if rel["source"] not in seen:
                        related_all.append(rel)
            except Exception as e:
                logger.warning("查找相关文档失败 (source=%s): %s", source, e)

        # 去重并按 score 排序
        by_source: dict[str, dict] = {}
        for rel in related_all:
            src = rel["source"]
            if src not in by_source or rel["score"] > by_source[src]["score"]:
                by_source[src] = rel
        return sorted(by_source.values(), key=lambda x: x["score"], reverse=True)[:limit]
