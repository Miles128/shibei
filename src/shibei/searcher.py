"""语义搜索引擎 — 仅 BM25 引擎

策略模式保留，但当前只实现 BM25Engine，未来如需扩展可继续注册。
"""

import logging
from abc import ABC, abstractmethod

from .config import Config
from .bm25 import BM25Index

logger = logging.getLogger(__name__)


class SearchEngine(ABC):
    """搜索引擎策略接口。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @abstractmethod
    def search(self, query: str, limit: int, tag: 'str | None' = None) -> dict:
        """执行搜索，返回 {"query": ..., "total": ..., "results": [...]}。"""
        ...


class BM25Engine(SearchEngine):
    """BM25 搜索引擎（SQLite FTS5）"""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def search(self, query: str, limit: int, tag: 'str | None' = None) -> dict:
        from .tokenizer import query_terms, tokenize_query

        expansion = None
        rewrite_info = None
        llm_rewrite_info = None
        effective_query = query

        if self.cfg.rewriter.enabled:
            from .rewriter import QueryRewriter
            rewriter = QueryRewriter(self.cfg.rewriter.rules)
            rewrite_info = rewriter.rewrite(query)
            effective_query = rewrite_info["rewritten"]
            if effective_query != query:
                logger.debug("规则改写查询: %s -> %s", query, effective_query)

            if self.cfg.rewriter.use_llm:
                from .llm_rewriter import LLMQueryRewriter
                llm_rewriter = LLMQueryRewriter(
                    self.cfg.llm,
                    max_terms=self.cfg.rewriter.max_terms,
                )
                llm_rewrite_info = llm_rewriter.rewrite(effective_query)
                if llm_rewrite_info.get("applied") and llm_rewrite_info.get("rewritten"):
                    effective_query = llm_rewrite_info["rewritten"]
                    logger.debug(
                        "LLM 改写查询: %s -> %s",
                        rewrite_info["rewritten"],
                        effective_query,
                    )

        tokenized_query = tokenize_query(effective_query)

        if self.cfg.expander.enabled:
            from .expander import QueryExpander
            expander = QueryExpander(
                synonyms_path=self.cfg.expander.synonyms_path or None,
                fuzzy=self.cfg.expander.fuzzy,
                fuzzy_max_distance=self.cfg.expander.fuzzy_max_distance,
                fuzzy_max_candidates=self.cfg.expander.fuzzy_max_candidates,
                min_query_len=self.cfg.expander.min_query_len,
            )
            expansion = expander.expand(effective_query)
            if expansion["expanded"]:
                tokenized_query = tokenize_query(expansion["expanded"])
                logger.debug("扩展查询: %s -> %s", effective_query, expansion["expanded"])

        bm25 = BM25Index(str(self.cfg.db_path_expanded / "fts5.db"))

        # 相关性门槛：把同义词/模糊候选归到同一组，组内任一命中即算覆盖
        expansions = (expansion or {}).get("expansions") or {}
        fuzzy_map = (expansion or {}).get("fuzzy") or {}
        groups_map: dict[str, list[str]] = {
            term: [term] for term in query_terms(effective_query)
        }
        for source in (expansions, fuzzy_map):
            for word, alts in source.items():
                groups_map.setdefault(word, [word]).extend(alts)
        coverage_groups = [
            list(dict.fromkeys(alts)) for alts in groups_map.values()
        ]

        # 轻量向量混合（可选）：开启且 embedding 可用时走三路 RRF
        hybrid_info = None
        query_vec = None
        if self.cfg.vector.enabled:
            from .embedding import Embedder

            embedder = Embedder(
                provider=self.cfg.vector.provider,
                model=self.cfg.vector.model,
                base_url=self.cfg.llm.base_url,
                timeout=self.cfg.llm.timeout,
                batch_size=self.cfg.vector.batch_size,
            )
            if embedder.available():
                vecs = embedder.embed([effective_query])
                if vecs:
                    query_vec = vecs[0]
                    hybrid_info = {
                        "provider": self.cfg.vector.provider,
                        "model": self.cfg.vector.model,
                        "min_score": self.cfg.vector.min_score,
                        "add_new_rows": self.cfg.vector.add_new_rows,
                    }

        if query_vec is not None:
            results = bm25.search_hybrid(
                query,
                limit,
                tag,
                tokenized_query=tokenized_query,
                query_vec=query_vec,
                min_similarity=self.cfg.vector.min_score,
                add_new_rows=self.cfg.vector.add_new_rows,
                coverage_groups=coverage_groups,
            )
        else:
            results = bm25.search(
                query, limit, tag,
                tokenized_query=tokenized_query,
                coverage_groups=coverage_groups,
            )

        result = {
            "query": query,
            "query_used": effective_query,
            "total": len(results),
            "results": results,
        }
        if rewrite_info:
            result["rewrite"] = rewrite_info
        if llm_rewrite_info:
            result["llm_rewrite"] = llm_rewrite_info
        if expansion:
            result["expansion"] = expansion
        if hybrid_info:
            result["hybrid"] = hybrid_info
        return result


_ENGINES: dict[str, type[SearchEngine]] = {
    "bm25": BM25Engine,
}


def register_engine(name: str, engine_cls: type[SearchEngine]) -> None:
    """注册自定义搜索引擎。"""
    _ENGINES[name] = engine_cls
    logger.info("注册搜索引擎: %s", name)


class Searcher:
    """BM25 搜索入口，支持记忆召回。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        include_memories: bool = True,
    ) -> dict:
        engine_name = self.cfg.storage.search_engine
        engine_cls = _ENGINES.get(engine_name)
        if engine_cls is None:
            logger.warning("Unknown search_engine '%s', fallback to bm25", engine_name)
            engine_cls = BM25Engine

        engine = engine_cls(self.cfg)
        fetch_limit = limit
        if self.cfg.rerank.enabled:
            fetch_limit = max(limit, self.cfg.rerank.top_k)
        result = engine.search(query, fetch_limit, tag)

        if self.cfg.rerank.enabled:
            from .rerank import Reranker

            reranker = Reranker(self.cfg.rerank)
            if reranker.available():
                reranked = reranker.rerank(query, result.get("results", []), self.cfg.rerank.top_k)
                result["results"] = reranked[:limit]
                result["total"] = len(result["results"])
                result["rerank"] = {"model": self.cfg.rerank.model, "candidates": len(reranked)}

        if include_memories:
            result["memories"] = self._search_memories(query, limit)

        if self.cfg.related.enabled:
            result["related"] = self._find_related(result.get("results", []))
        else:
            result["related"] = []
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
                "memory_type": r.get("memory_type", ""),
                "priority": r.get("priority", 0),
                "scene_name": r.get("scene_name", ""),
                "confidence": r.get("confidence", 0.0),
                "source_message_ids": r.get("source_message_ids", ""),
            }
            for r in rows
        ]

    def _find_related(
        self,
        results: list[dict],
        top_k: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """为前 top_k 个结果查找相关文档。"""
        if not results:
            return []

        top_k = self.cfg.related.top_k if top_k is None else top_k
        limit = self.cfg.related.limit if limit is None else limit

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
