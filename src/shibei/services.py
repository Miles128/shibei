"""Shibei 服务层 — 统一入口"""

import logging
from collections.abc import Callable

from .config import load_config

logger = logging.getLogger(__name__)


class Shibei:
    """中文语义知识库顶层 API。

    Usage:
        brain = Shibei("config.yaml")
        brain.import_all()
        results = brain.search("银行风控")
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        include_memories: bool = True,
    ) -> dict:
        from .searcher import Searcher
        return Searcher(self.config).search(
            query, limit, tag, include_memories=include_memories
        )

    def context(
        self,
        message: str,
        limit: int = 5,
        tag: 'str | None' = None,
        write_hints: bool = True,
    ) -> dict:
        """从用户消息构建答前上下文包（文档 + 记忆 + 写侧提示）。"""
        from .context import build_context
        return build_context(
            self.config,
            message=message,
            limit=limit,
            tag=tag,
            write_hints=write_hints,
        )

    def import_all(self, dirs: 'list[str] | None' = None, full: bool = False,
                   file_pattern: 'str | None' = None,
                   progress: 'Callable[[int, int], None] | None' = None) -> dict:
        from .importer import Importer
        return Importer(self.config).run(
            dirs, full=full, file_pattern=file_pattern, progress=progress
        )

    def list_sources(self, limit: int = 0, offset: int = 0) -> dict:
        from .importer import list_sources
        return list_sources(self.config, limit=limit, offset=offset)

    def clean_orphans(
        self,
        dirs: 'list[str] | None' = None,
        dry_run: bool = False,
    ) -> dict:
        from .importer import clean_orphans
        return clean_orphans(self.config, dirs=dirs, dry_run=dry_run)

    def dream(
        self,
        housekeeping: 'dict | None' = None,
        format: str = "brief",
        persist: bool = True,
    ) -> dict:
        from .dreamer import dream
        return dream(
            self.config.db_path_expanded,
            housekeeping=housekeeping,
            format=format,
            persist=persist,
        )

    # ---------- 对话记忆 ----------

    def _memory_store(self):
        from .memory import MemoryStore
        return MemoryStore(self.config.db_path_expanded)

    def remember(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: 'str | None' = None,
    ) -> dict:
        """写入一条 Agent 对话记忆。"""
        store = self._memory_store()
        return store.add(
            fact=fact,
            category=category,
            source=source,
            tags=tags,
            expires_at=expires_at,
        )

    def search_memories(self, query: str, limit: int = 5,
                        category: 'str | None' = None) -> list[dict]:
        """单独搜索对话记忆。"""
        return self._memory_store().search(query, limit=limit, category=category)

    def list_memories(self, limit: int = 100,
                      category: 'str | None' = None) -> list[dict]:
        """列出所有有效记忆（默认排除已被替代的版本）。"""
        return self._memory_store().list(limit=limit, category=category)

    def forget_memory(self, memory_id: str) -> bool:
        """删除一条记忆。"""
        return self._memory_store().forget(memory_id)

    def count_memories(self, category: 'str | None' = None) -> int:
        """统计有效记忆数量。"""
        return self._memory_store().count(category=category)

    def cleanup_memories(self) -> int:
        """清理已过期的对话记忆，返回删除条数。"""
        return self._memory_store().cleanup_expired()

    def update_memory(
        self,
        memory_id: str,
        fact: str,
        tags: 'str | None' = None,
    ) -> bool:
        """更新一条记忆内容（用于外部 Agent 执行压缩/修正）。"""
        return self._memory_store().update(memory_id, fact, tags=tags)

    def consolidate(
        self,
        threshold: float = 0.6,
        min_cluster_size: int = 2,
        max_chunks: int = 500,
        apply: bool = False,
    ) -> dict:
        """整理知识库：发现相似 chunk 聚类，可选直接合并（零 LLM 依赖）。"""
        from .consolidator import consolidate
        return consolidate(
            self.config.db_path_expanded,
            threshold=threshold,
            min_cluster_size=min_cluster_size,
            max_chunks=max_chunks,
            apply=apply,
        )

    def apply_consolidation(self, clusters: list[dict]) -> dict:
        """根据外部 Agent 提供的 cluster 摘要直接应用合并（零 LLM 依赖）。"""
        from .consolidator import apply_consolidation
        return apply_consolidation(self.config.db_path_expanded, clusters)
